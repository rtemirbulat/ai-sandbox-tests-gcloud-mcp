from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class AppSpec:
    name: str
    repo_url: str
    branch: str
    commit_sha: str
    build_pack: str  # static | nixpacks | dockerfile
    publish_directory: str
    domain: str
    ports_exposes: str = "80"


class CoolifyError(Exception):
    pass


class CoolifyClient(Protocol):
    def create_application(self, spec: AppSpec) -> str: ...
    def update_source(self, uuid: str, spec: AppSpec) -> None: ...
    def deploy(self, uuid: str) -> str: ...
    def deployment_status(self, deployment_uuid: str) -> str: ...
    def application_url(self, uuid: str) -> str: ...
    def delete_application(self, uuid: str) -> None: ...


class FakeCoolifyClient:
    def __init__(self):
        self.apps: dict[str, AppSpec] = {}
        self.deployments: dict[str, str] = {}
        self._n = 0

    def create_application(self, spec: AppSpec) -> str:
        self._n += 1
        uuid = f"fake-app-{self._n}"
        self.apps[uuid] = spec
        return uuid

    def update_source(self, uuid: str, spec: AppSpec) -> None:
        if uuid not in self.apps:
            raise CoolifyError("unknown app")
        self.apps[uuid] = spec

    def deploy(self, uuid: str) -> str:
        if uuid not in self.apps:
            raise CoolifyError("unknown app")
        dep = f"fake-dep-{uuid}"
        self.deployments[dep] = "finished"
        return dep

    def deployment_status(self, deployment_uuid: str) -> str:
        return self.deployments.get(deployment_uuid, "failed")

    def application_url(self, uuid: str) -> str:
        return self.apps[uuid].domain

    def delete_application(self, uuid: str) -> None:
        self.apps.pop(uuid, None)


class HttpCoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        project_uuid: str,
        server_uuid: str,
        environment_name: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self._c = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )
        self.project_uuid = project_uuid
        self.server_uuid = server_uuid
        self.environment_name = environment_name

    def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = self._c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise CoolifyError(f"Coolify {method} {path}: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise CoolifyError(f"Coolify {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def create_application(self, spec: AppSpec) -> str:
        body = {
            "project_uuid": self.project_uuid,
            "server_uuid": self.server_uuid,
            "environment_name": self.environment_name,
            "git_repository": spec.repo_url,
            "git_branch": spec.branch,
            "git_commit_sha": spec.commit_sha,
            "build_pack": spec.build_pack,
            "name": spec.name,
            "domains": spec.domain,
            "ports_exposes": spec.ports_exposes,
            "instant_deploy": False,
        }
        if spec.build_pack == "static":
            body["publish_directory"] = spec.publish_directory
        if spec.build_pack == "nixpacks":
            body["is_static"] = True
            body["publish_directory"] = spec.publish_directory
        return self._req("POST", "/applications/public", json=body)["uuid"]

    def update_source(self, uuid: str, spec: AppSpec) -> None:
        self._req(
            "PATCH",
            f"/applications/{uuid}",
            json={"git_branch": spec.branch, "git_commit_sha": spec.commit_sha},
        )

    def deploy(self, uuid: str) -> str:
        data = self._req("POST", "/deploy", params={"uuid": uuid, "force": "true"})
        deps = data.get("deployments") or []
        if not deps:
            raise CoolifyError("Coolify не вернул deployment_uuid")
        return deps[0]["deployment_uuid"]

    def deployment_status(self, deployment_uuid: str) -> str:
        return self._req("GET", f"/deployments/{deployment_uuid}").get("status", "unknown")

    def application_url(self, uuid: str) -> str:
        fqdn = self._req("GET", f"/applications/{uuid}").get("fqdn") or ""
        return fqdn.split(",")[0]

    def delete_application(self, uuid: str) -> None:
        self._req(
            "DELETE",
            f"/applications/{uuid}",
            params={"delete_configurations": "true", "delete_volumes": "true"},
        )


def make_coolify_client(settings) -> CoolifyClient:
    if settings.coolify_fake:
        return FakeCoolifyClient()
    return HttpCoolifyClient(
        settings.coolify_url,
        settings.coolify_token,
        project_uuid=settings.coolify_project_uuid,
        server_uuid=settings.coolify_server_uuid,
        environment_name=settings.coolify_environment_name,
    )
