import sqlite3
from xml.dom import minidom


def get_song_id(cur, mp3_path):
    res = cur.execute("SELECT * FROM media_file WHERE path = ?", [mp3_path]).fetchone()
    if not res:
        print(f"{mp3_path} not found in navidrome")
        return None
    id_ = res[0]
    return id_


def get_song_rating(cur, id_):
    res = cur.execute(f"SELECT * FROM annotation WHERE user_id='30cc0679-5e51-4698-bfe1-b4d3a42ec530' AND item_type='media_file' AND item_id=?", [id_]).fetchone()
    if not res:
        #print(f"No annotation found for {id_}")
        return 0
    return res[5]


def set_navidrome_rating(cur, song_id, rating):
    try:
        res = cur.execute("INSERT INTO annotation (user_id, item_id, item_type, rating) VALUES (?, ?, ?, ?)", ('30cc0679-5e51-4698-bfe1-b4d3a42ec530', song_id, 'media_file', int(rating)))
    except sqlite3.IntegrityError:
        res = cur.execute("UPDATE annotation SET rating=? where user_id=? and item_id=? and item_type=?", (int(rating), '30cc0679-5e51-4698-bfe1-b4d3a42ec530', song_id, 'media_file' ))
    return res
        

def main():
    dom = minidom.parse('foo_playcount_stats.xml')
    con = sqlite3.connect('backup_exclude/navidrome.db')
    cur = con.cursor()
    for entry in dom.getElementsByTagName('Entry'):
        try:
            rating = int(float(entry.attributes['RatingFriendly'].value))
        except KeyError:
            rating = 0
        if rating < 1:
            continue
        item = entry.getElementsByTagName('Item')[0]
        path = item.attributes['Path'].value.replace("G:\\MP3s\\", "").replace('\\', '/')
        if 'MP3s_overflow' in path:
            continue
        song_id = get_song_id(cur, path)
        if not song_id:
            continue
        navi_rating = get_song_rating(cur, song_id)

        # only change rating if the original migration to navidrome failed and the navi rating is at 0:
        if navi_rating == 0 and navi_rating != rating:
            print(f"Setting {path} from rating {navi_rating} to {rating}")
            set_navidrome_rating(cur, song_id, rating)
    cur.execute("COMMIT;")
    
if __name__ == '__main__':
    main()
