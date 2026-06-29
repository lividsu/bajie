import logging
import time
from pathlib import Path


CACHE_DIRS = ["image_cache", "file_cache"]
TENANT_CACHE_DIRS = ["images", "files", "generated_images"]


def cleanup_old_files(retention_days: int):
    if retention_days <= 0:
        return

    cutoff = time.time() - retention_days * 86400
    root = Path(__file__).parent.parent

    for dir_name in CACHE_DIRS:
        cache_dir = root / dir_name
        if not cache_dir.exists():
            continue
        for f in cache_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    logging.info(f"[cleanup] deleted expired file: {f.name}")
                except Exception as e:
                    logging.warning(f"[cleanup] failed to delete {f.name}: {e}")


def cleanup_tenant_files(tenant_configs):
    for tenant_config in tenant_configs:
        retention_days = tenant_config.limits.file_retention_days
        if retention_days <= 0:
            continue

        cutoff = time.time() - retention_days * 86400
        for dir_name in TENANT_CACHE_DIRS:
            cache_dir = tenant_config.cache_dir / dir_name
            if not cache_dir.exists():
                continue
            for f in cache_dir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    try:
                        f.unlink()
                        logging.info(
                            "[cleanup] deleted expired tenant file: "
                            f"tenant={tenant_config.tenant_id} file={f.name}"
                        )
                    except Exception as e:
                        logging.warning(f"[cleanup] failed to delete tenant file {f.name}: {e}")
