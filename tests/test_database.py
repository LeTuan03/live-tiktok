"""Database, migrations and repository tests."""

from __future__ import annotations

from sqlalchemy import text

from app.database.repositories.comment_repo import CommentRepository
from app.database.repositories.playlist_repo import PlaylistRepository
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository


def test_product_crud(db):
    repo = ProductRepository()
    product = repo.create({"name": "Áo thun Nam Premium", "price": 199000, "sku": "AT-001"})
    assert product.id is not None
    assert repo.get_by_id(product.id).sku == "AT-001"
    assert repo.update(product.id, {"price": 179000}).price == 179000
    assert repo.toggle_active(product.id) is False
    assert repo.count(is_active=True) == 0
    assert repo.delete(product.id) is True
    assert repo.get_by_id(product.id) is None


def test_product_search_and_categories(db):
    repo = ProductRepository()
    repo.create({"name": "Son môi đỏ", "category": "Mỹ phẩm"})
    repo.create({"name": "Nồi chiên", "category": "Gia dụng"})
    products, total = repo.get_all(search="son")
    assert total == 1 and products[0].name == "Son môi đỏ"
    assert repo.get_categories() == ["Gia dụng", "Mỹ phẩm"]


def test_script_versions(session):
    product = ProductRepository().create({"name": "Giày Sneaker", "price": 499000})
    script_repo = ScriptRepository(session)
    script = script_repo.create(product_id=product.id, title="Kịch bản", content="Nội dung v1", style="energetic")
    assert script.version == 1 and script.is_current
    updated = script_repo.update_content(script.id, "Nội dung v2", change_note="sửa")
    assert updated.version == 2
    assert len(script_repo.get_versions(script.id)) == 2
    # A new script for the same product becomes current, the old one is archived
    s2 = script_repo.create(product_id=product.id, title="Kịch bản 2", content="x")
    assert s2.is_current and not script_repo.get_by_id(script.id).is_current


def test_voice_repository_default(session):
    repo = VoiceRepository(session)
    p1 = repo.create_profile(name="A", provider="edge_tts", voice_id="vi-VN-NamMinhNeural", is_default=True)
    p2 = repo.create_profile(name="B", provider="edge_tts", voice_id="vi-VN-HoaiMyNeural", is_default=True)
    assert repo.get_default_profile().id == p2.id
    assert not repo.get_profile_by_id(p1.id).is_default


def test_comment_repository(session):
    repo = CommentRepository(session)
    event = repo.create_comment_event(username="Viewer1", content="Giá bao nhiêu shop ơi?", category="price")
    sug = repo.create_suggestion(comment_id=event.id, suggested_reply="Dạ giá 199k ạ!")
    assert sug.status == "pending"
    assert repo.update_suggestion(sug.id, status="approved").status == "approved"
    assert len(repo.list_recent_comments()) == 1


def test_playlist_items_reorder(session):
    prod = ProductRepository()
    a = prod.create({"name": "A"})
    b = prod.create({"name": "B"})
    c = prod.create({"name": "C"})
    repo = PlaylistRepository(session)
    pl = repo.create_playlist("Live tối")
    ia = repo.add_item(pl.id, product_id=a.id)
    ib = repo.add_item(pl.id, product_id=b.id)
    ic = repo.add_item(pl.id, product_id=c.id)
    assert [i.product_id for i in sorted(repo.get_by_id(pl.id, fresh=True).items, key=lambda i: i.sort_order)] == [a.id, b.id, c.id]
    assert repo.move_item(ic.id, -1)
    order = [i.product_id for i in sorted(repo.get_by_id(pl.id, fresh=True).items, key=lambda i: i.sort_order)]
    assert order == [a.id, c.id, b.id]
    assert repo.remove_item(ia.id)
    order = [i.sort_order for i in sorted(repo.get_by_id(pl.id, fresh=True).items, key=lambda i: i.sort_order)]
    assert order == [1, 2]
    assert repo.update_item(ib.id, is_enabled=False).is_enabled is False


def test_migrations_are_idempotent_and_upgrade_old_schema(tmp_path):
    """An old database (no platform columns) is upgraded in place without data loss."""
    from sqlalchemy import create_engine

    from app.database.migrations import CURRENT_SCHEMA_VERSION, column_exists, get_schema_version, run_migrations

    db_file = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, name VARCHAR(500) NOT NULL, status VARCHAR(20), created_at DATETIME, updated_at DATETIME)"))
        conn.execute(text("INSERT INTO live_sessions (name, status) VALUES ('old', 'stopped')"))

    version = run_migrations(engine)
    assert version == CURRENT_SCHEMA_VERSION
    with engine.connect() as conn:
        assert column_exists(conn, "live_sessions", "platform")
        assert conn.execute(text("SELECT name FROM live_sessions")).scalar() == "old"
        assert get_schema_version(conn) == CURRENT_SCHEMA_VERSION

    # Running again changes nothing
    assert run_migrations(engine) == CURRENT_SCHEMA_VERSION
    engine.dispose()
