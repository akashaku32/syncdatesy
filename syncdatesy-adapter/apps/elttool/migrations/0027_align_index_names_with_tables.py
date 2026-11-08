from django.db import migrations


class Migration(migrations.Migration):
    """
    Align index names with table names after prefix removal.
    
    After removing the 'elttool_' prefix, indexes were renamed from 
    'elttool_sourcemodel_*' to 'sourcemodel_*', but tables were renamed 
    to 'source_model' (with underscore). This migration fixes the mismatch
    by renaming indexes to match their table naming convention.
    
    Example:
    - Table: source_model
    - Old index: sourcemodel_pkey
    - New index: source_model_pkey
    """

    dependencies = [
        ("elttool", "0026_strip_elttool_prefix_from_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            sql=r"""
            DO $$
            DECLARE
                rec record;
                old_prefix text;
                new_prefix text;
                new_index_name text;
                table_name_pattern text;
            BEGIN
                -- Mapping of old index prefixes (without underscore) to new table names (with underscore)
                -- This handles the mismatch where indexes use camelCase but tables use snake_case
                FOR rec IN
                    SELECT 
                        pi.indexname,
                        pi.tablename,
                        pi.indexdef
                    FROM pg_indexes pi
                    WHERE pi.schemaname = 'public'
                      AND pi.indexname NOT LIKE 'pg_%'
                      -- Only process indexes that don't already match table naming
                      AND pi.indexname NOT LIKE pi.tablename || '_%'
                LOOP
                    -- Skip if index already matches table name pattern
                    IF rec.indexname LIKE rec.tablename || '_%' THEN
                        CONTINUE;
                    END IF;
                    
                    -- Map common old patterns to new table names
                    -- Use regexp_replace to replace old prefix with new table name
                    -- sourcemodel -> source_model
                    IF rec.indexname LIKE 'sourcemodel_%' AND rec.tablename = 'source_model' THEN
                        new_index_name := regexp_replace(rec.indexname, '^sourcemodel_', 'source_model_');
                    -- sourceattribute -> source_attribute
                    ELSIF rec.indexname LIKE 'sourceattribute_%' AND rec.tablename = 'source_attribute' THEN
                        new_index_name := regexp_replace(rec.indexname, '^sourceattribute_', 'source_attribute_');
                    -- sourceconfig -> source_config
                    ELSIF rec.indexname LIKE 'sourceconfig_%' AND rec.tablename = 'source_config' THEN
                        new_index_name := regexp_replace(rec.indexname, '^sourceconfig_', 'source_config_');
                    -- sourcetableselection -> source_table_selection
                    ELSIF rec.indexname LIKE 'sourcetableselection_%' AND rec.tablename = 'source_table_selection' THEN
                        new_index_name := regexp_replace(rec.indexname, '^sourcetableselection_', 'source_table_selection_');
                    -- sourcedeltatable -> source_delta_table
                    ELSIF rec.indexname LIKE 'sourcedeltatable_%' AND rec.tablename = 'source_delta_table' THEN
                        new_index_name := regexp_replace(rec.indexname, '^sourcedeltatable_', 'source_delta_table_');
                    -- communicationsupport -> communication_support
                    ELSIF rec.indexname LIKE 'communicationsupport_%' AND rec.tablename = 'communication_support' THEN
                        new_index_name := regexp_replace(rec.indexname, '^communicationsupport_', 'communication_support_');
                    -- datedirectory -> date_directory
                    ELSIF rec.indexname LIKE 'datedirectory_%' AND rec.tablename = 'date_directory' THEN
                        new_index_name := regexp_replace(rec.indexname, '^datedirectory_', 'date_directory_');
                    -- errorlogentry -> error_log_entry
                    ELSIF rec.indexname LIKE 'errorlogentry_%' AND rec.tablename = 'error_log_entry' THEN
                        new_index_name := regexp_replace(rec.indexname, '^errorlogentry_', 'error_log_entry_');
                    -- tablemapping -> table_mapping
                    ELSIF rec.indexname LIKE 'tablemapping_%' AND rec.tablename = 'table_mapping' THEN
                        new_index_name := regexp_replace(rec.indexname, '^tablemapping_', 'table_mapping_');
                    -- migrationrecord -> migration_record
                    ELSIF rec.indexname LIKE 'migrationrecord_%' AND rec.tablename = 'migration_record' THEN
                        new_index_name := regexp_replace(rec.indexname, '^migrationrecord_', 'migration_record_');
                    -- scheduledjob -> scheduled_job
                    ELSIF rec.indexname LIKE 'scheduledjob_%' AND rec.tablename = 'scheduled_job' THEN
                        new_index_name := regexp_replace(rec.indexname, '^scheduledjob_', 'scheduled_job_');
                    -- roleaccess -> role_access
                    ELSIF rec.indexname LIKE 'roleaccess_%' AND rec.tablename = 'role_access' THEN
                        new_index_name := regexp_replace(rec.indexname, '^roleaccess_', 'role_access_');
                    -- destinationmodel -> destination_model
                    ELSIF rec.indexname LIKE 'destinationmodel_%' AND rec.tablename = 'destination_model' THEN
                        new_index_name := regexp_replace(rec.indexname, '^destinationmodel_', 'destination_model_');
                    -- destinationattribute -> destination_attribute
                    ELSIF rec.indexname LIKE 'destinationattribute_%' AND rec.tablename = 'destination_attribute' THEN
                        new_index_name := regexp_replace(rec.indexname, '^destinationattribute_', 'destination_attribute_');
                    -- destinationconfig -> destination_config
                    ELSIF rec.indexname LIKE 'destinationconfig_%' AND rec.tablename = 'destination_config' THEN
                        new_index_name := regexp_replace(rec.indexname, '^destinationconfig_', 'destination_config_');
                    -- destinationtableselection -> destination_table_selection
                    ELSIF rec.indexname LIKE 'destinationtableselection_%' AND rec.tablename = 'destination_table_selection' THEN
                        new_index_name := regexp_replace(rec.indexname, '^destinationtableselection_', 'destination_table_selection_');
                    ELSE
                        -- Skip if no mapping found
                        CONTINUE;
                    END IF;
                    
                    -- Only rename if new name is different and doesn't already exist
                    IF new_index_name IS NOT NULL 
                       AND new_index_name != rec.indexname 
                       AND NOT EXISTS (
                           SELECT 1 FROM pg_indexes 
                           WHERE schemaname = 'public' 
                             AND indexname = new_index_name
                       ) THEN
                        EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, new_index_name);
                        RAISE NOTICE 'Renamed index % to %', rec.indexname, new_index_name;
                    END IF;
                    
                    new_index_name := NULL;
                END LOOP;
            END$$;
            """,
            reverse_sql=r"""
            DO $$
            DECLARE
                rec record;
                old_index_name text;
            BEGIN
                -- Reverse mapping: convert underscore table names back to camelCase index names
                FOR rec IN
                    SELECT 
                        pi.indexname,
                        pi.tablename
                    FROM pg_indexes pi
                    WHERE pi.schemaname = 'public'
                      AND pi.indexname LIKE pi.tablename || '_%'
                      AND pi.tablename LIKE '%\_%'  -- Only tables with underscores
                LOOP
                    -- Map back: source_model -> sourcemodel
                    IF rec.tablename = 'source_model' AND rec.indexname LIKE 'source_model_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^source_model_', 'sourcemodel_');
                    -- source_attribute -> sourceattribute
                    ELSIF rec.tablename = 'source_attribute' AND rec.indexname LIKE 'source_attribute_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^source_attribute_', 'sourceattribute_');
                    -- source_config -> sourceconfig
                    ELSIF rec.tablename = 'source_config' AND rec.indexname LIKE 'source_config_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^source_config_', 'sourceconfig_');
                    -- source_table_selection -> sourcetableselection
                    ELSIF rec.tablename = 'source_table_selection' AND rec.indexname LIKE 'source_table_selection_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^source_table_selection_', 'sourcetableselection_');
                    -- source_delta_table -> sourcedeltatable
                    ELSIF rec.tablename = 'source_delta_table' AND rec.indexname LIKE 'source_delta_table_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^source_delta_table_', 'sourcedeltatable_');
                    -- communication_support -> communicationsupport
                    ELSIF rec.tablename = 'communication_support' AND rec.indexname LIKE 'communication_support_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^communication_support_', 'communicationsupport_');
                    -- date_directory -> datedirectory
                    ELSIF rec.tablename = 'date_directory' AND rec.indexname LIKE 'date_directory_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^date_directory_', 'datedirectory_');
                    -- error_log_entry -> errorlogentry
                    ELSIF rec.tablename = 'error_log_entry' AND rec.indexname LIKE 'error_log_entry_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^error_log_entry_', 'errorlogentry_');
                    -- table_mapping -> tablemapping
                    ELSIF rec.tablename = 'table_mapping' AND rec.indexname LIKE 'table_mapping_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^table_mapping_', 'tablemapping_');
                    -- migration_record -> migrationrecord
                    ELSIF rec.tablename = 'migration_record' AND rec.indexname LIKE 'migration_record_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^migration_record_', 'migrationrecord_');
                    -- scheduled_job -> scheduledjob
                    ELSIF rec.tablename = 'scheduled_job' AND rec.indexname LIKE 'scheduled_job_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^scheduled_job_', 'scheduledjob_');
                    -- role_access -> roleaccess
                    ELSIF rec.tablename = 'role_access' AND rec.indexname LIKE 'role_access_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^role_access_', 'roleaccess_');
                    -- destination_model -> destinationmodel
                    ELSIF rec.tablename = 'destination_model' AND rec.indexname LIKE 'destination_model_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^destination_model_', 'destinationmodel_');
                    -- destination_attribute -> destinationattribute
                    ELSIF rec.tablename = 'destination_attribute' AND rec.indexname LIKE 'destination_attribute_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^destination_attribute_', 'destinationattribute_');
                    -- destination_config -> destinationconfig
                    ELSIF rec.tablename = 'destination_config' AND rec.indexname LIKE 'destination_config_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^destination_config_', 'destinationconfig_');
                    -- destination_table_selection -> destinationtableselection
                    ELSIF rec.tablename = 'destination_table_selection' AND rec.indexname LIKE 'destination_table_selection_%' THEN
                        old_index_name := regexp_replace(rec.indexname, '^destination_table_selection_', 'destinationtableselection_');
                    ELSE
                        CONTINUE;
                    END IF;
                    
                    -- Only rename if old name doesn't already exist
                    IF old_index_name IS NOT NULL 
                       AND old_index_name != rec.indexname 
                       AND NOT EXISTS (
                           SELECT 1 FROM pg_indexes 
                           WHERE schemaname = 'public' 
                             AND indexname = old_index_name
                       ) THEN
                        EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, old_index_name);
                        RAISE NOTICE 'Reverted index % to %', rec.indexname, old_index_name;
                    END IF;
                    
                    old_index_name := NULL;
                END LOOP;
            END$$;
            """,
        ),
    ]

