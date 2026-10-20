{target}
Объясни пользователю ответ:
- state и stage: где сейчас задание (queued, running/<стадия>, succeeded, blocked, failed, expired).
- checks: перечисли стадии с результатом; для failed и error приведи summary.
- succeeded: дай url и expires_at, напомни про TTL 14 дней и что приложение доступно всем, кто знает адрес.
- blocked: что исправить в коде и что после фикса нужен новый commit и новый validate_and_deploy.
- failed: предложи один повтор с новым idempotency_key, затем эскалацию администратору с job_id.
- queued или running: предложи проверить снова через 30-60 секунд.
