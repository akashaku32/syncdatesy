from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


# Legacy DBs may already have proxy_agent from 0106_add_proxy_agent_model
# (migration file removed). CreateModel would DuplicateTable — use IF NOT EXISTS
# / ADD COLUMN IF NOT EXISTS so both greenfield and batch stay consistent.
_PROXY_AGENT_SQL = """
CREATE TABLE IF NOT EXISTS proxy_agent (
    agent_id serial PRIMARY KEY,
    agent_name varchar(255) NOT NULL,
    agent_uid varchar(64) NOT NULL UNIQUE,
    bootstrap_token jsonb NULL,
    client_cert_pem jsonb NULL,
    client_cert_not_after timestamptz NULL,
    progress_signing_secret_hash varchar(64) NULL,
    progress_signing_secret jsonb NULL,
    last_progress_at timestamptz NULL,
    config_downloaded_at timestamptz NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    cmp_id integer NOT NULL REFERENCES company(cmp_id) DEFERRABLE INITIALLY DEFERRED,
    created_by_id integer NULL REFERENCES "user"(user_id) DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE proxy_agent ALTER COLUMN agent_name TYPE varchar(255);
ALTER TABLE proxy_agent ALTER COLUMN bootstrap_token DROP NOT NULL;
ALTER TABLE proxy_agent ALTER COLUMN client_cert_pem DROP NOT NULL;
ALTER TABLE proxy_agent ALTER COLUMN client_cert_not_after DROP NOT NULL;

ALTER TABLE proxy_agent ADD COLUMN IF NOT EXISTS progress_signing_secret_hash varchar(64) NULL;
ALTER TABLE proxy_agent ADD COLUMN IF NOT EXISTS progress_signing_secret jsonb NULL;
ALTER TABLE proxy_agent ADD COLUMN IF NOT EXISTS last_progress_at timestamptz NULL;
ALTER TABLE proxy_agent ADD COLUMN IF NOT EXISTS updated_at timestamptz NULL;
UPDATE proxy_agent SET updated_at = COALESCE(updated_at, created_at, NOW()) WHERE updated_at IS NULL;
ALTER TABLE proxy_agent ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE proxy_agent ALTER COLUMN updated_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS proxy_agent_agent_uid_idx ON proxy_agent (agent_uid);
CREATE INDEX IF NOT EXISTS proxy_agent_cmp_id_agent_u_idx ON proxy_agent (cmp_id, agent_uid);
CREATE INDEX IF NOT EXISTS proxy_agent_created_by_id_b01d4cd8 ON proxy_agent (created_by_id);

-- Model allows multiple agents per company; drop legacy one-agent-per-cmp unique.
ALTER TABLE proxy_agent DROP CONSTRAINT IF EXISTS proxy_agent_cmp_id_key;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0123_reencrypt_credentials_master_key'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='ProxyAgent',
                    fields=[
                        ('agent_id', models.AutoField(primary_key=True, serialize=False)),
                        ('agent_name', models.CharField(max_length=255)),
                        ('agent_uid', models.CharField(db_index=True, max_length=64, unique=True)),
                        ('bootstrap_token', models.JSONField(blank=True, null=True)),
                        ('client_cert_pem', models.JSONField(blank=True, null=True)),
                        ('client_cert_not_after', models.DateTimeField(blank=True, null=True)),
                        (
                            'progress_signing_secret_hash',
                            models.CharField(
                                blank=True,
                                help_text=(
                                    'SHA-256 hex digest of progress_signing_secret '
                                    '(raw secret never stored in plaintext).'
                                ),
                                max_length=64,
                                null=True,
                            ),
                        ),
                        (
                            'progress_signing_secret',
                            models.JSONField(
                                blank=True,
                                help_text='AES-GCM encrypted progress HMAC secret.',
                                null=True,
                            ),
                        ),
                        ('last_progress_at', models.DateTimeField(blank=True, null=True)),
                        ('config_downloaded_at', models.DateTimeField(blank=True, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        (
                            'cmp',
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name='proxy_agents',
                                to='elttool.company',
                            ),
                        ),
                        (
                            'created_by',
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name='proxy_agents_created',
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        'db_table': 'proxy_agent',
                    },
                ),
                migrations.AddIndex(
                    model_name='proxyagent',
                    index=models.Index(
                        fields=['cmp', 'agent_uid'],
                        name='proxy_agent_cmp_id_agent_u_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=_PROXY_AGENT_SQL,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
