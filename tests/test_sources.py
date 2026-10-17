import json
import unittest
from unittest import mock

from cratedigger.sources.archive import build_query, parse_item
from cratedigger.sources.base import SourceError, order_downloads
from cratedigger.sources.jamendo import JamendoSource, parse_track, parse_waveform
from cratedigger.sources.youtube import friendly_error, parse_entry, tags_from_info

# Shaped like real responses from the Jamendo v3 API, with invented names.
JAMENDO_TRACK = {
    "id": "1848357", "name": "Harbour  Lights", "duration": 272, "artist_name": "Low Tide",
    "album_name": "Night Ferries", "license_ccurl": "http://creativecommons.org/licenses/by-nc-sa/3.0/",
    "releasedate": "2021-04-11", "image": "https://usercontent.jamendo.com?type=album&id=1&width=300",
    "audio": "https://prod-1.storage.jamendo.com/?trackid=1848357&format=mp32",
    "audiodownload": "https://prod-1.storage.jamendo.com/download/track/1848357/mp32/",
    "shareurl": "https://www.jamendo.com/track/1848357",
    "waveform": json.dumps({"peaks": [0, 10, 50, 100, 40, 0] * 100}),
    "musicinfo": {"vocalinstrumental": "instrumental", "tags": {"genres": ["electronic", "house"]}},
    "audiodownload_allowed": True,
}


class JamendoTest(unittest.TestCase):
    def test_parse_track(self):
        t = parse_track(JAMENDO_TRACK)
        self.assertEqual((t.title, t.artist, t.album, t.year), ("Harbour Lights", "Low Tide", "Night Ferries", "2021"))
        self.assertEqual(t.license, "CC BY-NC-SA 3.0")
        self.assertEqual(t.genres, ["electronic", "house"])
        self.assertEqual([d["ext"] for d in t.downloads], [".flac", ".mp3"])
        self.assertTrue(t.downloads[0]["url"].endswith("/download/track/1848357/flac/"))
        self.assertEqual(len(t.waveform), 240)
        self.assertEqual(max(t.waveform), 1.0)
        self.assertEqual(t.uid, "jamendo:1848357")

    def test_download_not_allowed_is_skipped(self):
        self.assertIsNone(parse_track({**JAMENDO_TRACK, "audiodownload_allowed": False}))
        self.assertIsNone(parse_track({**JAMENDO_TRACK, "audiodownload": ""}))

    def test_waveform_garbage(self):
        self.assertIsNone(parse_waveform("not json"))
        self.assertIsNone(parse_waveform(json.dumps({"peaks": []})))

    def test_search_requires_client_id_and_reports_api_errors(self):
        store = mock.Mock()
        store.get.return_value = mock.Mock(jamendo_client_id="")
        source = JamendoSource(store, mock.Mock())
        self.assertFalse(source.status()[0])
        with self.assertRaises(SourceError):
            source.search("house")

        store.get.return_value = mock.Mock(jamendo_client_id="abc")
        response = mock.Mock(status_code=200)
        response.json.return_value = {"headers": {"status": "failed", "error_message": "Your credential is not authorized."}}
        session = mock.Mock()
        session.get.return_value = response
        with self.assertRaisesRegex(SourceError, "not authorized"):
            JamendoSource(store, session).search("house")

        response.json.return_value = {"headers": {"status": "success"}, "results": [JAMENDO_TRACK]}
        tracks = JamendoSource(store, session).search("house")
        self.assertEqual(len(tracks), 1)
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["client_id"], "abc")
        self.assertEqual(params["search"], "house")

    def test_order_downloads(self):
        downloads = parse_track(JAMENDO_TRACK).downloads
        self.assertEqual([d["ext"] for d in order_downloads(downloads, False)], [".mp3", ".flac"])
        self.assertEqual([d["ext"] for d in order_downloads(downloads, True)], [".flac", ".mp3"])


# Shaped like https://archive.org/metadata/<id> for a netlabel release.
ARCHIVE_ITEM = {
    "metadata": {
        "identifier": "lowtide-ferries", "title": "Night Ferries EP", "creator": "Low Tide",
        "licenseurl": "http://creativecommons.org/licenses/by-nc-sa/2.5/se/",
        "subject": "low tide; techno; minimal; netlabel", "date": "2011-01-06",
        "collection": ["some-label", "netlabels"],
    },
    "files": [
        {"name": "02 Undertow.flac", "source": "original", "format": "Flac", "title": "Undertow", "track": "2", "length": "04:12"},
        {"name": "02 Undertow.mp3", "source": "derivative", "format": "VBR MP3", "original": "02 Undertow.flac", "length": "252.1"},
        {"name": "02 Undertow.ogg", "source": "derivative", "format": "Ogg Vorbis", "original": "02 Undertow.flac"},
        {"name": "01 Harbour Lights.mp3", "source": "original", "format": "VBR MP3", "title": "Harbour Lights",
         "creator": "Low Tide", "track": "1", "length": "291.6"},
        {"name": "cover.jpg", "source": "original", "format": "JPEG"},
        {"name": "lowtide-ferries_archive.torrent", "source": "metadata", "format": "Archive BitTorrent"},
    ],
}


class ArchiveTest(unittest.TestCase):
    def test_parse_item_groups_formats_per_song(self):
        tracks = parse_item(ARCHIVE_ITEM)
        self.assertEqual([t.title for t in tracks], ["Harbour Lights", "Undertow"])
        first, second = tracks
        self.assertEqual(first.artist, "Low Tide")
        self.assertEqual(first.duration, 291.6)
        self.assertEqual(second.duration, 252.0)
        self.assertEqual(second.album, "Night Ferries EP")
        self.assertEqual([d["ext"] for d in second.downloads], [".flac", ".mp3", ".ogg"])
        self.assertTrue(second.preview_url.endswith("/02%20Undertow.mp3"))
        self.assertEqual(second.license, "CC BY-NC-SA 2.5")
        self.assertEqual(second.genres, ["techno", "minimal"])
        self.assertEqual(second.year, "2011")
        self.assertTrue(first.page_url.endswith("/details/lowtide-ferries"))

    def test_items_without_open_license_are_skipped(self):
        item = json.loads(json.dumps(ARCHIVE_ITEM))
        item["metadata"]["licenseurl"] = ""
        self.assertEqual(parse_item(item), [])

    def test_query_escaping(self):
        q = build_query('AC/DC "deep house" (live) NOT')
        self.assertIn("AC\\/DC", q)
        self.assertIn('"deep house"', q)
        self.assertIn("\\(live\\)", q)
        self.assertIn(" AND not", q)
        self.assertTrue(q.endswith("AND collection:(netlabels) AND mediatype:(audio)"))
        with self.assertRaises(SourceError):
            build_query("   ")


class YouTubeTest(unittest.TestCase):
    def test_parse_entry(self):
        entry = {"_type": "url", "ie_key": "Youtube", "id": "Ab3dEf6hIj0", "url": "https://www.youtube.com/watch?v=Ab3dEf6hIj0",
                 "title": "Low Tide - Harbour Lights (Official Audio)", "channel": "Low Tide", "duration": 291.0,
                 "view_count": 1200}
        t = parse_entry(entry)
        self.assertEqual((t.artist, t.title, t.duration, t.license), ("Low Tide", "Harbour Lights", 291.0, "YouTube"))
        self.assertEqual(t.page_url, "https://www.youtube.com/watch?v=Ab3dEf6hIj0")
        self.assertEqual(parse_entry(entry, cc_only=True).license, "CC BY")

    def test_parse_entry_skips_non_videos(self):
        base = {"ie_key": "Youtube", "id": "Ab3dEf6hIj0", "title": "x", "duration": 200}
        self.assertIsNone(parse_entry({**base, "ie_key": "YoutubeTab", "id": "UCabcdefghijklmnopqrstuv"}))
        self.assertIsNone(parse_entry({**base, "url": "https://www.youtube.com/shorts/Ab3dEf6hIj0"}))
        self.assertIsNone(parse_entry({**base, "live_status": "is_live"}))
        self.assertIsNone(parse_entry({**base, "duration": 12}))
        self.assertIsNone(parse_entry({**base, "id": "not-a-video-id"}))

    def test_tags_prefer_music_metadata(self):
        info = {"title": "Harbour Lights (Official Video)", "channel": "Low Tide - Topic", "artists": ["Low Tide", "Mira"],
                "track": "Harbour Lights", "album": "Night Ferries", "release_year": 2021,
                "license": "Creative Commons Attribution license (reuse allowed)"}
        tags = tags_from_info(info)
        self.assertEqual(tags["artist"], "Low Tide, Mira")
        self.assertEqual(tags["license"], "CC BY")
        self.assertEqual(tags["year"], "2021")
        tags = tags_from_info({"title": "Low Tide - Undertow [HD]", "channel": "Uploads", "upload_date": "20190305"})
        self.assertEqual((tags["artist"], tags["title"], tags["year"], tags["license"]), ("Low Tide", "Undertow", "2019", "YouTube"))

    def test_friendly_errors(self):
        self.assertIn("Settings", friendly_error("ERROR: [youtube] abc: Sign in to confirm you’re not a bot."))
        self.assertIn("Deno", friendly_error("ERROR: [youtube] abc: Requested format is not available."))
        self.assertEqual(friendly_error("ERROR: [youtube] abc: Private video. Sign in if you've been granted access"),
                         "This video is private.")
        self.assertEqual(friendly_error("ERROR: [youtube] abc123: Something odd happened"), "Something odd happened")
        self.assertEqual(friendly_error("ERROR: [youtube:search] x: Unable to download API page: <urlopen error "
                                        "[Errno 8] nodename nor servname provided>"),
                         "Couldn't reach YouTube. Check your internet connection.")
        self.assertEqual(friendly_error("ERROR: [youtube] abc: New failure (caused by Foo); please report this issue"),
                         "New failure")


if __name__ == "__main__":
    unittest.main()
