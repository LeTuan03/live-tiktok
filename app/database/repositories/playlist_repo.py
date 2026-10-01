"""Playlist Repository for managing Playlists and PlaylistItems."""

from __future__ import annotations

from typing import Optional, Sequence
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.database.models.playlist import Playlist, PlaylistItem


class PlaylistRepository:
    """Repository for managing Playlist and PlaylistItem entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_playlist(
        self,
        name: str,
        description: Optional[str] = None,
        is_loop: bool = True,
        gap_seconds: float = 5.0,
    ) -> Playlist:
        playlist = Playlist(
            name=name,
            description=description,
            is_loop=is_loop,
            gap_seconds=gap_seconds,
            is_active=True,
        )
        self.session.add(playlist)
        self.session.commit()
        self.session.refresh(playlist)
        return playlist

    def list_playlists(self) -> Sequence[Playlist]:
        return self.session.scalars(
            select(Playlist).order_by(desc(Playlist.updated_at))
        ).all()

    def get_by_id(self, playlist_id: int) -> Optional[Playlist]:
        return self.session.get(Playlist, playlist_id)

    def add_item(
        self,
        playlist_id: int,
        product_id: Optional[int] = None,
        media_id: Optional[int] = None,
        script_id: Optional[int] = None,
        audio_id: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> PlaylistItem:
        playlist = self.get_by_id(playlist_id)
        current_max = len(playlist.items) if playlist else 0

        item = PlaylistItem(
            playlist_id=playlist_id,
            product_id=product_id,
            media_id=media_id,
            script_id=script_id,
            audio_id=audio_id,
            duration_seconds=duration_seconds,
            sort_order=current_max + 1,
            is_enabled=True,
        )
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def delete_playlist(self, playlist_id: int) -> bool:
        playlist = self.get_by_id(playlist_id)
        if not playlist:
            return False
        self.session.delete(playlist)
        self.session.commit()
        return True
