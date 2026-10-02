"""Playlist Repository for managing Playlists and PlaylistItems."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.database.models.playlist import Playlist, PlaylistItem


class PlaylistRepository:
    """Repository for managing Playlist and PlaylistItem entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    # ----------------------------------------------------------- playlists
    def create_playlist(
        self,
        name: str,
        description: Optional[str] = None,
        is_loop: bool = True,
        gap_seconds: float = 5.0,
        loop_count: int = 1,
    ) -> Playlist:
        playlist = Playlist(
            name=name,
            description=description,
            is_loop=is_loop,
            loop_count=loop_count,
            gap_seconds=gap_seconds,
            is_active=True,
        )
        self.session.add(playlist)
        self._commit()
        self.session.refresh(playlist)
        return playlist

    def update_playlist(self, playlist_id: int, **fields) -> Optional[Playlist]:
        playlist = self.get_by_id(playlist_id)
        if not playlist:
            return None
        for key, value in fields.items():
            if hasattr(playlist, key) and key not in ("id", "created_at", "items"):
                setattr(playlist, key, value)
        self._commit()
        self.session.refresh(playlist)
        return playlist

    def list_playlists(self) -> Sequence[Playlist]:
        self.session.expire_all()
        return self.session.scalars(
            select(Playlist)
            .options(selectinload(Playlist.items))
            .order_by(desc(Playlist.updated_at))
        ).all()

    def get_by_id(self, playlist_id: int, fresh: bool = False) -> Optional[Playlist]:
        if fresh:
            self.session.expire_all()
        return self.session.get(Playlist, playlist_id)

    def delete_playlist(self, playlist_id: int) -> bool:
        playlist = self.get_by_id(playlist_id)
        if not playlist:
            return False
        self.session.delete(playlist)
        self._commit()
        return True

    # --------------------------------------------------------------- items
    def add_item(
        self,
        playlist_id: int,
        product_id: Optional[int] = None,
        media_id: Optional[int] = None,
        script_id: Optional[int] = None,
        audio_id: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> PlaylistItem:
        playlist = self.get_by_id(playlist_id)
        current_max = max((i.sort_order for i in playlist.items), default=0) if playlist else 0

        item = PlaylistItem(
            playlist_id=playlist_id,
            product_id=product_id,
            media_id=media_id,
            script_id=script_id,
            audio_id=audio_id,
            duration_seconds=duration_seconds,
            sort_order=current_max + 1,
            is_enabled=True,
            notes=notes,
        )
        self.session.add(item)
        self._commit()
        self.session.refresh(item)
        if playlist:
            self.session.refresh(playlist)
        return item

    def get_item(self, item_id: int) -> Optional[PlaylistItem]:
        return self.session.get(PlaylistItem, item_id)

    def update_item(self, item_id: int, **fields) -> Optional[PlaylistItem]:
        item = self.get_item(item_id)
        if not item:
            return None
        for key, value in fields.items():
            if hasattr(item, key) and key not in ("id", "created_at", "playlist_id"):
                setattr(item, key, value)
        self._commit()
        self.session.refresh(item)
        return item

    def remove_item(self, item_id: int) -> bool:
        item = self.get_item(item_id)
        if not item:
            return False
        playlist_id = item.playlist_id
        self.session.delete(item)
        self._commit()
        self._renumber(playlist_id)
        return True

    def move_item(self, item_id: int, direction: int) -> bool:
        """Move an item up (-1) or down (+1) within its playlist."""
        item = self.get_item(item_id)
        if not item:
            return False
        playlist = self.get_by_id(item.playlist_id, fresh=True)
        if not playlist:
            return False
        items = sorted(playlist.items, key=lambda i: i.sort_order)
        idx = next((n for n, i in enumerate(items) if i.id == item_id), -1)
        new_idx = idx + direction
        if idx < 0 or new_idx < 0 or new_idx >= len(items):
            return False
        items[idx], items[new_idx] = items[new_idx], items[idx]
        for n, it in enumerate(items, start=1):
            it.sort_order = n
        self._commit()
        return True

    def _renumber(self, playlist_id: int) -> None:
        playlist = self.get_by_id(playlist_id, fresh=True)
        if not playlist:
            return
        for n, it in enumerate(sorted(playlist.items, key=lambda i: i.sort_order), start=1):
            it.sort_order = n
        self._commit()

    def count_items(self, playlist_id: int) -> int:
        playlist = self.get_by_id(playlist_id, fresh=True)
        return len(playlist.items) if playlist else 0
