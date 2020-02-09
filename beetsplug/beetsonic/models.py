# -*- coding: utf-8 -*-
"""
Model to get music information from beets.
"""
from __future__ import (
    division,
    absolute_import,
    print_function,
    unicode_literals,
)

import functools
import glob
import os
import random
from datetime import datetime

import enum
import six
from beets.ui import decargs
from beetsplug.lyrics import LyricsPlugin

from beetsplug.beetsonic import utils

BEET_MUSIC_FOLDER_ID = 1


@enum.unique
class BeetIdType(enum.Enum):
    album = 'album'
    item = 'item'
    artist = 'artist'
    playlist = 'playlist'

    @staticmethod
    def get_type(value):
        """
        Return the BeetIdType for an Id value.
        :param value: the Id value.
        :return: the BeetIdType Enum.
        """
        value_parts = value.split(':')
        if len(value_parts) <= 1:
            raise ValueError('Invalid Id: {}'.format(value))
        id_type = BeetIdType(value_parts[0])
        if id_type is BeetIdType.album or id_type is BeetIdType.item:
            id_value = int(value_parts[1])
        else:
            id_value = value_parts[1]

        return id_type, id_value

    @staticmethod
    def get_artist_id(name):
        """
        Return the Subsonic id for an artist.
        :param name: The name of the artist.
        :return: The Subsonic Id for that artist.
        """
        return BeetIdType.artist.value + ':' + name

    @staticmethod
    def get_album_id(album_id):
        """
        Return the Subsonic id for an album.
        :param album_id: The beets internal Id for an album.
        :return: The Subsonic Id for that album.
        """
        return BeetIdType.album.value + ':' + str(album_id)

    @staticmethod
    def get_item_id(item_id):
        """
        Return the Subsonic id for a item.
        :param item_id: The beets internal Id for an Item.
        :return: The Subsonic Id for that Item.
        """
        return BeetIdType.item.value + ':' + str(item_id)

    @staticmethod
    def get_playlist_id(playlist_name):
        """
        Return the Subsonic id for a playlist.
        :param playlist_name: The name of the playlist.
        :return: The Subsonic Id for that Playlist.
        """
        return BeetIdType.playlist.value + ':' + playlist_name


class BeetsModel(object):
    def __init__(self, lib):
        self.lib = lib
        self.basedir = lib.directory
        if not isinstance(self.basedir, six.string_types):
            self.basedir = self.basedir.decode()

    def _resolve_path(self, path, relative=False):
        if not path:
            return None
        if not isinstance(path, six.string_types):
            path = path.decode()
        return os.path.relpath(path, self.basedir) if relative else path

    @staticmethod
    def _create_artist(name, **kwargs):
        # Since beets doesn't track artist ids, we'll make the id the name
        # of the artist, prefixed with the string 'artist:', in order to
        # differentiate between Artist and other metadata types.
        return utils.create_artist(BeetIdType.get_artist_id(name), name,
                                   **kwargs)

    @staticmethod
    def _create_artist_id3(name, album_count, **kwargs):
        return utils.create_artist_id3(BeetIdType.get_artist_id(name), name,
                                   album_count, **kwargs)

    def _create_song(self, item):
        """
        Create a Child object from beets' Item.
        :param item: The beet's Item object.
        :return: The Child object.
        """
        item_id = BeetIdType.get_item_id(item.id)
        album_id = None
        if item.album_id:
            album_id = BeetIdType.get_album_id(item.album_id)
        path = self._resolve_path(item.path, relative=True)
        return utils.create_song(
            item_id, item.title, album=item.album, artist=item.artist,
            year=item.year, genre=item.genre, coverArt=album_id,
            path=path, parent=album_id, track=item.track, duration=item.length,
            type=utils.get_music_type(), isVideo=False,
            suffix=item.format.lower(),
        )

    @staticmethod
    def _create_album(album):
        """
        Create a Child object from beets' Album.
        :param album: The beet's Album object.
        :return: The Child object.
        """
        art_path = None
        if album['artpath']:
            art_path = BeetIdType.get_album_id(album['id'])
        return utils.create_album(
            BeetIdType.get_album_id(album['id']), album['album'],
            artist=album['albumartist'], year=album['year'],
            genre=album['genre'], coverArt=art_path,
            parent=BeetIdType.get_artist_id(album['albumartist'])
        )

    def get_album_artists(self):
        """
        Get all album artists
        :return: List of Artist objects
        """
        with self.lib.transaction() as tx:
            rows = tx.query(
                'SELECT DISTINCT albumartist FROM albums ORDER BY albumartist'
            )
        return [self._create_artist(row[0]) for row in rows]

    def get_album_artists_id3(self):
        """
        Get all album artists
        :return: List of ArtistID3 objects
        """
        with self.lib.transaction() as tx:
            rows = tx.query(
                'SELECT albumartist, COUNT(1) FROM albums GROUP BY albumartist ORDER BY albumartist'
            )
        return [self._create_artist_id3(row[0], row[1]) for row in rows]

    def get_singletons(self):
        """
        Get all the singletons in Child objects
        :return: Child objects for singletons
        """
        results = self.lib.items('singleton:true')
        return [self._create_song(item) for item in results]

    def get_last_modified(self):
        """
        Get the timestamp of the last modified operation
        :return: the Unix timestamp of the last modified operation
        """
        with self.lib.transaction() as tx:
            rows = tx.query('SELECT max(mtime) FROM items')
        return rows[0][0]

    @staticmethod
    def get_music_folders():
        """
        Get the Music Folders object
        :return: the Music Folders object
        """
        return utils.create_music_folders([
            utils.create_music_folder(BEET_MUSIC_FOLDER_ID,
                                      name='beets music folder')
        ])

    def _get_albums_from_artist(self, artist_name, columns):
        """
        Get the Album objects from an artist.
        :param artist_name: Name of the artist.
        :param columns: The columns to fetch from the table.
        :return: A list of Album objects.
        """
        with self.lib.transaction() as tx:
            query = 'SELECT {} FROM albums WHERE albumartist=?'.format(
                ', '.join(columns)
            )
            rows = tx.query(query, (artist_name,))
        albums = [dict(zip(columns, row)) for row in rows]
        return albums

    def get_music_directory(self, object_id):
        beet_id = BeetIdType.get_type(object_id)
        children = []
        parent = None
        if beet_id[0] is BeetIdType.album:
            album = self.lib.get_album(beet_id[1])
            name = album.album
            parent = BeetIdType.get_artist_id(album.albumartist)
            children = [self._create_song(item) for item in album.items()]
        elif beet_id[0] is BeetIdType.artist:
            name = beet_id[1]
            columns = ['id', 'album', 'albumartist', 'year', 'genre', 'artpath']
            albums = self._get_albums_from_artist(beet_id[1], columns)
            for album in albums:
                children.append(self._create_album(album))
        else:
            # It is the Item here
            item = self.lib.get_item(beet_id[1])
            name = item.title
            parent = BeetIdType.get_album_id(item.album_id)
        return utils.create_directory(object_id, name, children,
                                      parent=parent)

    def get_random_songs(self, size=10, genre=None, from_year=None,
                         to_year=None, music_folder_id=None):
        """
        Get random songs wrapped in a Songs object
        :param size: Maximum number of songs to return.
        :param genre: Only returns songs belonging to this genre.
        :param from_year: Only return songs published after or in this year.
        :param to_year: Only return songs published before or in this year.
        :param music_folder_id: Only return songs in this music folder.
        :return: a Songs object.
        """
        songs = []
        if not music_folder_id or music_folder_id == str(BEET_MUSIC_FOLDER_ID):
            # Adapted from the Random plugin
            query_parts = []
            if genre:
                query_parts.append('genre:{}'.format(genre))
            if from_year or to_year:
                from_year = from_year or ''
                to_year = to_year or ''
                year_range = [from_year, to_year]
                query_parts.append('year:{}'.format('..'.join(year_range)))
            query = decargs(query_parts)
            result = list(self.lib.items(query))
            number = min(len(result), size)
            items = random.sample(result, number)
            songs = [self._create_song(item) for item in items]

        return utils.create_songs(songs)

    def get_song_location(self, id):
        id = BeetIdType.get_type(id)[1]
        item = self.lib.get_item(id)
        if not item:
            raise ValueError('Song with id {} not found'.format(id))
        return self._resolve_path(item.path)

    @staticmethod
    def get_user(username):
        return utils.create_user(
            username=username,
            scrobbling_enabled=False,
            admin_role=True,
            settings_role=False,
            download_role=True,
            upload_role=False,
            playlist_role=True,
            cover_art_role=True,
            comment_role=False,
            podcast_role=False,
            stream_role=True,
            jukebox_role=False,
            share_role=False,
            video_conversion_role=False,
            folder_ids=[BEET_MUSIC_FOLDER_ID]
        )

    def get_cover_art(self, object_id):
        """
        Return the cover art location for an Item, Album or Artist.
        :param object_id: The Id of the object.
        :return: The path to the cover art file.
        """
        beet_id = BeetIdType.get_type(object_id)
        location = None
        if beet_id[0] is BeetIdType.album:
            album = self.lib.get_album(beet_id[1])
            if album:
                location = album.artpath or None
        elif beet_id[0] is BeetIdType.artist:
            columns = ['artpath']
            albums = self._get_albums_from_artist(beet_id[1], columns)
            albums = [album for album in albums if album['artpath']]
            if len(albums) > 0:
                location = albums[0]['artpath']
        elif beet_id[0] is BeetIdType.item:
            item = self.lib.get_item(beet_id[1])
            if item:
                album = item.get_album()
                if album and album.artpath:
                    location = album.artpath

        return self._resolve_path(location)

    @staticmethod
    def get_lyrics(artist, title):
        # For now let's return an empty lyrics if either artist or lyrics is
        # not passed in
        empty_lyrics = utils.create_lyrics('', artist=artist, title=title)
        if not artist or not title:
            return empty_lyrics
        lyrics_plugin = LyricsPlugin()
        lyrics = lyrics_plugin.get_lyrics(artist, title)
        if not lyrics:
            return empty_lyrics
        return utils.create_lyrics(lyrics, artist=artist, title=title)

    def get_genres(self):
        album_query = 'SELECT genre, count(genre) FROM albums GROUP BY genre'
        item_query = 'SELECT genre, count(genre) FROM items GROUP BY genre'
        with self.lib.transaction() as tx:
            distinct_album_genres = tx.query(album_query)
            distinct_item_genres = tx.query(item_query)
        album_genre_map = {row[0]: int(row[1]) for row in distinct_album_genres}
        item_genre_map = {row[0]: int(row[1]) for row in distinct_item_genres}
        all_genres = set(album_genre_map.keys()).union(
            set(item_genre_map.keys())
        )
        genre_objs = [
            utils.create_genre(
                genre,
                album_genre_map[genre] if genre in album_genre_map else 0,
                item_genre_map[genre] if genre in item_genre_map else 0)
            for genre in all_genres]
        return utils.create_genres(genre_objs)

    def get_playlists(self, playlist_dir, username):
        """
        Get all m3u or m3u8 playlist from a directory, matching them with beets'
        internal DB to ensure that only songs that are present in beets are
        returned in the Playlists object.
        :param playlist_dir: The directory to find playlists.
        :return: The bound Playlists object.
        """
        m3us = glob.glob(os.path.join(playlist_dir, '*.m3u*'))
        playlists = []
        for m3u in m3us:
            playlist = self._get_playlist(m3u, username)
            if playlist:
                playlists.append(playlist)
        return utils.create_playlists(playlists)

    def _get_playlist(self, location, username):
        try:
            playlist_filename = os.path.basename(location)
            playlist_name = os.path.splitext(playlist_filename)[0]
            # For now let's say the created date is the same as the last
            # modified date
            last_modified = datetime.fromtimestamp(os.path.getmtime(location))
            songs = utils.parse_m3u(location)

            duration = 0
            num_songs = len(songs)
            children = []

            if len(songs) > 0:
                songs = ['path:"{}"'.format(path) for path in songs]
                query = ', '.join(songs)
                items = self.lib.items(query)
                num_songs = len(items)
                duration = functools.reduce(
                    lambda length, item: length + item.length,
                    items,
                    0
                )
                children = [self._create_song(item) for item in items]
            return utils.create_playlist(
                children, [username],
                BeetIdType.get_playlist_id(playlist_filename),
                playlist_name, num_songs, duration, last_modified,
                last_modified)
        except IOError:
            return None

    def get_playlist(self, playlist_id, playlist_dir, username):
        """
        Get a playlist from a directory, matching the items in it with beets'
        internal DB to ensure that only songs that are present in beets are
        returned.
        :param playlist_id: The playlist id.
        :param playlist_dir: The playlist directory.
        :return: The bound Playlist object.
        """
        playlist_filename = BeetIdType.get_type(playlist_id)[1]
        location = os.path.join(playlist_dir, playlist_filename)
        return self._get_playlist(location, username)

    def get_album(self, album_id):
        """
        Get an AlbumWithSongsID3 object from an Id.
        :param album_id: The Id of the Album.
        :return: The AlbumWithSongsID3 object.
        """
        beet_id = BeetIdType.get_type(album_id)
        if beet_id[0] is not BeetIdType.album:
            raise ValueError('Wrong Album Id: {}'.format(album_id))
        with self.lib.transaction() as tx:
            album = self.lib.get_album(beet_id[1])
            items = album.items()

        children = [self._create_song(item) for item in items]
        return utils.create_album_with_songs_id3(
            id=BeetIdType.get_album_id(album.id),
            name=album.album,
            song_count=len(items),
            duration=sum(item.length for item in items),
            created=datetime.fromtimestamp(album.added),
            children=children,
            artist=album.albumartist,
            artistId=BeetIdType.get_artist_id(album.albumartist),
            coverArt=BeetIdType.get_album_id(album.id),
            year=album.year,
            genre=album.genre,
        )

    def get_song(self, item_id):
        """
        Get a Child object from an Id.
        :param item_id: The Id of the Item.
        :return: The Child object.
        """
        beet_id = BeetIdType.get_type(item_id)
        if beet_id[0] is not BeetIdType.item:
            raise ValueError('Wrong Item Id: {}'.format(item_id))
        item = self.lib.get_item(beet_id[1])
        return self._create_song(item)

    def get_artist_with_albums(self, artist_id):
        """
        Get an artist with associated albums from an artist id.
        :param artist_id: The id of the artist.
        :return: The ArtistWithAlbumsID3 object.
        """
        beet_id = BeetIdType.get_type(artist_id)
        if beet_id[0] is not BeetIdType.artist:
            raise ValueError('Wrong Artist Id: {}'.format(artist_id))
        albums = self.lib.albums('albumartist:{}'.format(beet_id[1]))
        if len(albums) == 0:
            raise EntityNotFoundError('Artist {} not found'.format(beet_id[1]))
        album_id3s = []
        for album in albums:
            items = album.items()
            album_id3s.append(
                utils.create_album_id3(
                    id=BeetIdType.get_album_id(album.id),
                    name=album.album,
                    song_count=len(items),
                    duration=sum(item.length for item in items),
                    created=datetime.fromtimestamp(album.added),
                    artist=album.albumartist,
                    artistId=BeetIdType.get_artist_id(album.albumartist),
                    coverArt=BeetIdType.get_album_id(album.id),
                    year=album.year,
                    genre=album.genre,
                )
            )
        return utils.create_artist_with_albums_id3(
            id=artist_id,
            name=beet_id[1],
            album_count=len(album_id3s),
            albums=album_id3s,
            coverArt=artist_id,
        )

    def get_artist_mbid(self, artist_id):
        beet_id = BeetIdType.get_type(artist_id)
        if beet_id[0] is not BeetIdType.artist:
            raise ValueError('Wrong Artist Id: {}'.format(artist_id))
        with self.lib.transaction() as tx:
            rows = tx.query('SELECT DISTINCT mb_albumartistid FROM albums WHERE albumartist=?', (beet_id[1],))
        if len(rows) < 1:
            raise EntityNotFoundError('Artist {} not found'.format(beet_id[1]))
        return rows[0][0]


class EntityNotFoundError(Exception):
    pass
