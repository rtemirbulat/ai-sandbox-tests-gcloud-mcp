import logging
import time

from sandbox_mcp.db.repo import Repository
from sandbox_mcp.db.schema import ensure_schema
from sandbox_mcp.db.session import make_engine, make_session_factory
from sandbox_mcp.services.coolify import CoolifyError, make_coolify_client
from sandbox_mcp.services.pipeline import run_job
from sandbox_mcp.settings import Settings

log = logging.getLogger(__name__)
SWEEP_INTERVAL_SECONDS = 3600


def tick(repo: Repository, settings, coolify, lease_seconds: int) -> bool:
    job = repo.claim_job(lease_seconds)
    if job is None:
        return False
    log.info("job %s claimed", job.job_id)
    run_job(job.job_id, repo, settings, coolify)
    log.info("job %s finished: %s", job.job_id, repo.get_job(job.job_id).state)
    return True


def sweep_expired(repo: Repository, coolify) -> int:
    n = 0
    for job in repo.expired_jobs():
        app = repo.get_application(job.app_id)
        try:
            if app and app.coolify_uuid:
                coolify.delete_application(app.coolify_uuid)
                repo.set_coolify_uuid(app.app_id, None)
            repo.update_job(job.job_id, state="expired", url=None)
            n += 1
        except CoolifyError as e:
            log.warning("expire %s failed: %s", job.job_id, e)
    return n


def run_worker(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    ensure_schema(engine)
    repo = Repository(make_session_factory(engine))
    coolify = make_coolify_client(settings)
    log.info("worker started (coolify_fake=%s)", settings.coolify_fake)
    last_sweep = 0.0
    while True:
        try:
            if time.time() - last_sweep > SWEEP_INTERVAL_SECONDS:
                sweep_expired(repo, coolify)
                last_sweep = time.time()
            if not tick(repo, settings, coolify, settings.lease_seconds):
                time.sleep(3)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            time.sleep(5)
