from typing import Any

from homeassistant.helpers.storage import Store
from pymammotion.http.model.http import ErrorInfo

from .const import DOMAIN


class MammotionConfigStore(Store):
    """A configuration store for Mammotion."""

    _STORAGE_VERSION = 1
    _STORAGE_MINOR_VERSION = 2
    _STORAGE_KEY = DOMAIN

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Migrate configuration to the new version."""
        if old_major_version < 2 and old_minor_version < 2:
            old_data["errors"] = {
                "error_codes": {},
                "err_code_list": [],
                "err_code_list_time": [],
            }
            error_codes: dict[str, ErrorInfo] | None = old_data.get("error_codes")
            err_code_list: list | None = old_data.get("err_code_list")
            err_code_list_time: list | None = old_data.get("err_code_list_time")
            if error_codes is not None:
                old_data["errors"]["error_codes"] = old_data["error_codes"]
                del old_data["error_codes"]
            if err_code_list is not None:
                old_data["errors"]["err_code_list"] = old_data["err_code_list"]
                del old_data["err_code_list"]
            if err_code_list_time is not None:
                old_data["errors"]["err_code_list_time"] = old_data[
                    "err_code_list_time"
                ]
                del old_data["err_code_list_time"]

        return old_data
