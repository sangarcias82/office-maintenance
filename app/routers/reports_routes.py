from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import Forbidden, get_lang, require_user
from app.db import get_db
from app.deps import render
from app.i18n import t
from app.models import AppUser
from app.services import catalog, reports as report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
@router.get("/")
def report_page(
    request: Request,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    maintenanceTypeId: Optional[int] = None,
    locationId: Optional[int] = None,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    if not user.can_view_reports():
        raise Forbidden(t(get_lang(request), "forbidden"))

    rows_src = report_service.filtered_requests(
        db,
        actor=user,
        from_date=fromDate,
        to_date=toDate,
        maintenance_type_id=maintenanceTypeId,
        location_id=locationId,
    )
    indicators = report_service.build_indicators(rows_src)
    unassigned = t(get_lang(request), "unassigned")
    detail = report_service.build_report_rows(rows_src, unassigned_label=unassigned)
    status_labels, status_values = report_service.chart_by_status(rows_src)
    type_labels, type_values = report_service.chart_by_type(rows_src)

    return render(
        request,
        "reports/index.html",
        current_user=user,
        title_key="report.title",
        indicators=indicators,
        rows=detail,
        from_date=fromDate or "",
        to_date=toDate or "",
        maintenance_type_id=maintenanceTypeId,
        location_id=locationId,
        maintenance_types=catalog.list_maintenance_types(db),
        locations=catalog.list_locations(db),
        status_chart_labels_json=json.dumps(status_labels),
        status_chart_values_json=json.dumps(status_values),
        type_chart_labels_json=json.dumps(type_labels),
        type_chart_values_json=json.dumps(type_values),
    )
