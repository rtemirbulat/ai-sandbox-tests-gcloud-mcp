from dataclasses import dataclass, field

REQUIREMENTS_VERSION = "R1"
ALLOWED_CLASSES = {"none", "internal", "personal", "financial"}
SENSITIVE = {"personal", "financial"}

TEST_REQUIREMENTS = [
    "Код хранится в публичном GitHub-репозитории; деплоится только проверенный commit.",
    "Секреты в репозитории запрещены (проверка Gitleaks блокирует выпуск).",
    "Уязвимости HIGH/CRITICAL в зависимостях и конфигурации блокируют выпуск (Trivy).",
    "Окружение TEST: TTL 14 дней, один контейнер, без доступа к корпоративным данным.",
    "Пользовательский Dockerfile допускается, но образ работает без root и без docker.sock.",
]


@dataclass
class Assessment:
    decision: str  # allowed | needs_clarification | rejected
    reason: str = ""
    requirements_version: str = REQUIREMENTS_VERSION
    requirements: list[str] = field(default_factory=list)


def assess(kind: str, data_classes: list[str]) -> Assessment:
    classes = set(data_classes or ["none"])
    if not classes <= ALLOWED_CLASSES:
        return Assessment(
            "rejected", f"Неизвестные классы данных: {sorted(classes - ALLOWED_CLASSES)}"
        )
    if kind == "unsupported":
        return Assessment(
            "rejected",
            "Не распознан тип проекта: нужен index.html, package.json со скриптом build "
            "или Dockerfile",
        )
    if classes & SENSITIVE:
        return Assessment(
            "needs_clarification",
            "Заявлены ПДн или финансовые данные: профиль TEST без ПДн не присваивается "
            "автоматически, нужно согласование владельца данных",
            requirements=TEST_REQUIREMENTS,
        )
    return Assessment("allowed", requirements=TEST_REQUIREMENTS)
