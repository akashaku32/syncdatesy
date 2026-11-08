from django.contrib import admin

from apps.elttool.models import (
    DestinationModel,
    PlanResourceConfig,
    SourceModel,
    WorkerNodePoolConfig,
)


@admin.register(SourceModel)
class SourceModelAdmin(admin.ModelAdmin):
    list_display = ("src_id", "name", "min_count", "max_count", "is_rtl", "display_name")
    list_editable = ("min_count", "max_count")
    search_fields = ("name", "display_name")


@admin.register(DestinationModel)
class DestinationModelAdmin(admin.ModelAdmin):
    list_display = ("dst_id", "name", "min_count", "max_count")
    list_editable = ("min_count", "max_count")
    search_fields = ("name",)


@admin.register(WorkerNodePoolConfig)
class WorkerNodePoolConfigAdmin(admin.ModelAdmin):
    list_display = ("pool_name", "machine_type", "allocatable_memory", "allocatable_cpu")
    readonly_fields = ("updated_at",)


@admin.register(PlanResourceConfig)
class PlanResourceConfigAdmin(admin.ModelAdmin):
    list_display = (
        "plan_name",
        "max_pods",
        "concurrency",
        "max_concurrency",
        "chunk_size_pct",
        "max_chunk_size",
        "max_inflight_tables",
        "memory_limit",
    )
    readonly_fields = ("updated_at",)
