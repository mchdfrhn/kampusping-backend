from sqlalchemy.orm import Session

from app.db.models.reference import MenuMapping


def get_menu_mapping_by_menu_id(db: Session, menu_id: int) -> MenuMapping | None:
    return db.query(MenuMapping).filter(MenuMapping.menu_id == menu_id).first()


def get_menu_mapping_by_id(db: Session, row_id: int) -> MenuMapping | None:
    return db.query(MenuMapping).filter(MenuMapping.id == row_id).first()


def list_menu_mappings(db: Session) -> list[MenuMapping]:
    return db.query(MenuMapping).order_by(MenuMapping.menu_id.asc()).all()


def create_menu_mapping(db: Session, *, menu_id: int, slug: str) -> MenuMapping:
    row = MenuMapping(menu_id=menu_id, slug=slug)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_menu_mapping(db: Session, row: MenuMapping, **fields) -> MenuMapping:
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_menu_mapping(db: Session, row: MenuMapping) -> None:
    db.delete(row)
    db.commit()
