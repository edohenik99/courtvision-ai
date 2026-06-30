"""Compatibility wrapper for the packaged collector dependency doctor."""

import sys

from courtvision.data_collection.doctor import (
    APPROVED_DISTRIBUTIONS,
    APPROVED_INSTALL_GROUPS,
    APPROVED_INSTALL_PACKAGES,
    FEATURE_REQUIREMENTS,
    PROJECT_ROOT,
    build_doctor_report,
    build_parser,
    format_report,
    inspect_dependencies,
    install_dependency_group,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
