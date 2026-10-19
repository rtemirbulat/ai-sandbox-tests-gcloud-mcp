from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from sandbox_mcp.db.models import Application, Job, UserToken, utcnow
from sandbox_mcp.ids import new_id, slugify


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    # applications
    def create_application(
        self,
        *,
        owner: str,
        repo_url: str,
        app_name: str,
        git_ref: str = "main",
        purpose: str,
        data_classes: list[str],
        kind: str,
        decision: str,
        requirements_version: str,
    ) -> Application:
        with self._sf() as s, s.begin():
            base = slugify(app_name)
            slug = base
            n = 2
            while s.scalar(select(Application).where(Application.slug == slug)) is not None:
                slug = f"{base}-{n}"
                n += 1
            app = Application(
                app_id=new_id("app"),
                owner=owner,
                slug=slug,
                repo_url=repo_url,
                git_ref=git_ref,
                app_name=app_name,
                purpose=purpose,
                data_classes=list(data_classes),
                kind=kind,
                decision=decision,
                requirements_version=requirements_version,
            )
            s.add(app)
            return app

    def get_application(self, app_id: str) -> Application | None:
        with self._sf() as s:
            return s.get(Application, app_id)

    def set_coolify_uuid(self, app_id: str, uuid: str | None) -> None:
        with self._sf() as s, s.begin():
            s.execute(
                update(Application).where(Application.app_id == app_id).values(coolify_uuid=uuid)
            )

    # jobs
    def get_or_create_job(
        self, app_id: str, commit_sha: str, env: str, idempotency_key: str
    ) -> tuple[Job, bool]:
        with self._sf() as s, s.begin():
            job = s.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            if job is not None:
                return job, False
            job = Job(
                job_id=new_id("job"),
                app_id=app_id,
                commit_sha=commit_sha,
                env=env,
                idempotency_key=idempotency_key,
            )
            s.add(job)
            return job, True

    def get_job(self, job_id: str) -> Job | None:
        with self._sf() as s:
            return s.get(Job, job_id)

    def latest_job_for_app(self, app_id: str) -> Job | None:
        with self._sf() as s:
            return s.scalar(
                select(Job).where(Job.app_id == app_id).order_by(Job.created_at.desc()).limit(1)
            )

    def claim_job(self, lease_seconds: int) -> Job | None:
        now = utcnow()
        with self._sf() as s, s.begin():
            q = (
                select(Job)
                .where(
                    (Job.state == "queued") | ((Job.state == "running") & (Job.lease_until < now))
                )
                .order_by(Job.created_at)
                .limit(1)
            )
            if s.get_bind().dialect.name == "postgresql":
                q = q.with_for_update(skip_locked=True)
            job = s.scalar(q)
            if job is None:
                return None
            job.state = "running"
            job.lease_until = now + timedelta(seconds=lease_seconds)
            return job

    def update_job(self, job_id: str, **fields) -> None:
        with self._sf() as s, s.begin():
            s.execute(update(Job).where(Job.job_id == job_id).values(**fields))

    def expired_jobs(self) -> list[Job]:
        with self._sf() as s:
            return list(
                s.scalars(
                    select(Job).where((Job.state == "succeeded") & (Job.expires_at < utcnow()))
                )
            )

    # user tokens (identity, see services/identity.py)
    def create_user_token(self, user: str) -> str:
        from sandbox_mcp.services.identity import new_token, token_hash

        token = new_token()
        with self._sf() as s, s.begin():
            s.add(UserToken(token_hash=token_hash(token), user=user))
        return token

    def user_for_token_hash(self, digest: str) -> str | None:
        with self._sf() as s:
            row = s.get(UserToken, digest)
            return row.user if row else None
