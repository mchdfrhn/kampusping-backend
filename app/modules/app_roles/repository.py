from sqlalchemy.orm import Session

from app.db.models.reference import AppAllowedRole


def list_active_app_allowed_roles(db: Session) -> list[AppAllowedRole]:
    return (
        db.query(AppAllowedRole)
        .filter(AppAllowedRole.is_active == 1)
        .order_by(AppAllowedRole.sort_order.asc(), AppAllowedRole.iam_role_id.asc())
        .all()
    )


def list_app_allowed_roles(db: Session) -> list[AppAllowedRole]:
    return (
        db.query(AppAllowedRole)
        .order_by(AppAllowedRole.sort_order.asc(), AppAllowedRole.iam_role_id.asc())
        .all()
    )


def get_app_allowed_role(db: Session, row_id: int) -> AppAllowedRole | None:
    return db.query(AppAllowedRole).filter(AppAllowedRole.id == row_id).first()


def get_app_allowed_role_by_iam_id(db: Session, iam_role_id: int) -> AppAllowedRole | None:
    return db.query(AppAllowedRole).filter(AppAllowedRole.iam_role_id == iam_role_id).first()


def create_app_allowed_role(
    db: Session,
    *,
    iam_role_id: int,
    display_name: str,
    auth_slug: str | None,
    role_type: str,
    sort_order: int,
    is_active: bool,
) -> AppAllowedRole:
    row = AppAllowedRole(
        iam_role_id=iam_role_id,
        display_name=display_name,
        auth_slug=auth_slug,
        role_type=role_type,
        sort_order=sort_order,
        is_active=1 if is_active else 0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_app_allowed_role(db: Session, row: AppAllowedRole, **fields) -> AppAllowedRole:
    for key, value in fields.items():
        if value is None:
            continue
        if key == "is_active":
            setattr(row, key, 1 if value else 0)
        else:
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_app_allowed_role(db: Session, row: AppAllowedRole) -> None:
    db.delete(row)
    db.commit()
