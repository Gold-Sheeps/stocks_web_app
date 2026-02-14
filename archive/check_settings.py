from app.core.config import settings
print(f"DB Host: {settings.postgres_host}")
print(f"DB Port: {settings.postgres_port}")
print(f"DB Name: {settings.postgres_db}")
print(f"DB User: {settings.postgres_user}")
# Mask password partially if possible, or just print len
print(f"DB Password: {settings.postgres_password}")
