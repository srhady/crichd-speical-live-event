import re
import subprocess
import logging
import datetime
import time

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# --- Configuration ---
CRICHD_BASE_URL = "https://crichd.com.co"
OUTPUT_M3U_FILE = "Live_Events.m3u"
EPG_URL = "https://github.com/epgshare01/share/raw/master/epg_ripper_ALL_SOURCES1.xml.gz"

logging.basicConfig(level=logging.INFO, format='%(message)s')

def run_command(command):
    try:
        result = subprocess.run(command, capture_output=True, shell=True, check=True, timeout=20)
        return result.stdout.decode('utf-8', errors='ignore')
    except Exception:
        return None

def clean_channel_name(name):
    name = re.sub(r'(\s*Live Stream(ing)?|\s*-\s*CricHD|\s*US\s*-|\s*-\s*Free|\s*Watch|\s*HD|\s*-\s*PSL T20 On|\s*Play\s*-\s*01)', '', name, flags=re.IGNORECASE)
    return " ".join(name.split())

def is_stream_working(stream_url, referrer):
    if not stream_url: return False
    command = f"curl -L -H 'Referer: {referrer}' --max-time 5 -s '{stream_url}' | head -n 1"
    output = run_command(command)
    if output and "#EXTM3U" in output:
        return True
    return False

# --- ১. হোমপেজ থেকে লাইভ ম্যাচ খোঁজার ফাংশন ---
def get_live_matches():
    logging.info(f"\n[*] হোমপেজ থেকে লাইভ ম্যাচ স্ক্যান করা হচ্ছে...")
    html = run_command(f"curl -sL {CRICHD_BASE_URL}")
    if not html: return []
    
    matches = []
    seen = set()
    
    # টেবিলের প্রতিটি সারি (tr) আলাদা করা
    tr_blocks = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    for tr_content in tr_blocks:
        # সারির ভেতর থেকে ম্যাচের লিংক খোঁজা
        link_match = re.search(r'<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>', tr_content, re.IGNORECASE)
        if link_match:
            url = link_match.group(1).strip()
            title = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()
            
            if url.startswith('/'):
                url = CRICHD_BASE_URL + url
                
            # মেনু, চ্যানেল বা অপ্রয়োজনীয় পেজ বাদ দেওয়া
            if CRICHD_BASE_URL in url and len(title) > 3 and '/channels/' not in url and not re.search(r'(score|telegram|contact|about|privacy|dmca)', url, re.IGNORECASE):
                if url not in seen:
                    matches.append((url, clean_channel_name(title)))
                    seen.add(url)
                    
    logging.info(f"[+] মোট {len(matches)} টি লাইভ ইভেন্ট পাওয়া গেছে!")
    return matches

# --- ৩. টোকেন চুরি করে আসল m3u8 লিংক বের করার ফাংশন ---
def extract_bhalocast_m3u8(player_url):
    try:
        player_id = player_url.split("id=")[1].split("&")[0]
        playerado_url = f"https://playerado.top/embed2.php?id={player_id}"
        
        embed_page_content = run_command(f"curl -sL '{playerado_url}'")
        if not embed_page_content: return None

        fid_match = re.search(r'fid\s*=\s*\"([^\"]+)\"', embed_page_content)
        v_con_match = re.search(r'v_con\s*=\s*\"([^\"]+)\"', embed_page_content)
        v_dt_match = re.search(r'v_dt\s*=\s*\"([^\"]+)\"', embed_page_content)
        if not (fid_match and v_con_match and v_dt_match): return None
        fid, v_con, v_dt = fid_match.group(1), v_con_match.group(1), v_dt_match.group(1)

        atplay_url = f"https://player0003.com/atplay.php?v={fid}&hello={v_con}&expires={v_dt}"
        atplay_page_content = run_command(f"curl -siL --user-agent \"Mozilla/5.0\" --referer \"https://playerado.top/\" '{atplay_url}'")
        if not atplay_page_content: return None

        func_name_match = re.search(r'player\.load\({source: (\w+)\(\),', atplay_page_content)
        if not func_name_match: return None
        func_name = func_name_match.group(1)

        func_def_match = re.search(r'function\s+' + func_name + r'\s*\(\)\s*{(.*?)}', atplay_page_content, re.DOTALL)
        if not func_def_match: return None
        func_body = func_def_match.group(1)

        base_url_var = re.search(r'var url = (\w+);', func_body).group(1)
        md5_var = re.search(r'url \+= "\?md5="\s*\+\s*(\w+);', func_body).group(1)
        expires_var = re.search(r'url \+= "&expires="\s*\+\s*(\w+);', func_body).group(1)
        s_var = re.search(r'url \+= "&s="\s*\+\s*(\w+);', func_body).group(1)

        md5 = re.search(r'var ' + md5_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)
        expires = re.search(r'var ' + expires_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)
        s_val = re.search(r'var ' + s_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)

        constructor_string = re.search(r'var ' + base_url_var + r'\s*=\s*(.*?);', atplay_page_content).group(1)
        real_base_url_var = constructor_string.split('+')[0].strip()
        
        base_url_str_with_plus = re.search(r"var " + real_base_url_var + r" = (.*?);", atplay_page_content).group(1)
        js_string_parts = re.findall(r"'(.*?)'", base_url_str_with_plus)
        base_url = "".join(js_string_parts)
        
        stream_path = f"/hls/{fid}.m3u8"
        return f"{base_url}{stream_path}?md5={md5}&expires={expires}&ch={fid}&s={s_val}"
    except Exception:
        return None

# --- ২. ম্যাচের ভেতরে ঢুকে চ্যানেল স্ক্যান করার ফাংশন ---
def get_match_streams(match_url, match_title):
    logging.info(f"\n➤ স্ক্যান করা হচ্ছে ম্যাচ: {match_title}")
    html = run_command(f"curl -sL '{match_url}'")
    if not html: return []

    streams = []
    tr_blocks = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    
    for tr_content in tr_blocks:
        # Watch বাটনের dadocric লিংক খোঁজা
        player_link_match = re.search(r"<a[^>]+href=['\"](https://(?:player\.)?dadocric\.st/player\.php\?id=[^\'\"]+)['\"]", tr_content)
        
        if player_link_match:
            player_url = player_link_match.group(1)
            
            # একই সারি থেকে চ্যানেলের নাম (যেমন: TNT Cricket) বের করা
            td_matches = re.findall(r'<td[^>]*>(.*?)</td>', tr_content, re.DOTALL | re.IGNORECASE)
            channel_name = "Event Channel"
            if len(td_matches) > 0:
                clean_td = re.sub(r'<[^>]+>', '', td_matches[0]).strip()
                if clean_td and "Channel Name" not in clean_td:
                    channel_name = clean_td
            
            logging.info(f"   - চ্যানেল পাওয়া গেছে: {channel_name}, টোকেন ডিক্রিপ্ট করা হচ্ছে...")
            stream_link = extract_bhalocast_m3u8(player_url)
            
            if stream_link:
                logging.info(f"     ✅ লিংক সফলভাবে উদ্ধার হয়েছে!")
                streams.append((channel_name, stream_link))
            else:
                logging.info(f"     ❌ লিংক উদ্ধারে ব্যর্থ।")
                
    return streams

# --- Main Execution ---
if __name__ == "__main__":
    all_channels = []
    
    # ১. সব লাইভ ম্যাচ নিয়ে আসা
    live_matches = get_live_matches()
    
    # ২. প্রতিটি ম্যাচের ভেতরের চ্যানেল স্ক্যান করা
    for match_url, match_title in live_matches:
        channels = get_match_streams(match_url, match_title)
        
        for ch_name, stream_url in channels:
            if is_stream_working(stream_url, "https://player0003.com/"):
                all_channels.append({
                    'match_name': match_title,
                    'channel_name': ch_name,
                    'url': stream_url,
                    'referrer': "https://player0003.com/"
                })
        time.sleep(1) # সার্ভার যেন ব্লক না মারে তাই ১ সেকেন্ড ব্রেক
        
    total_channels = len(all_channels)
    
    # সময় সেট করা
    update_time = ""
    if ZoneInfo:
        try:
            dhaka_tz = ZoneInfo('Asia/Dhaka')
            update_time = datetime.datetime.now(dhaka_tz).strftime('%Y-%m-%d %I:%M:%S %p')
        except Exception:
            update_time = datetime.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
            
    # প্লেলিস্ট তৈরি করা
    with open(OUTPUT_M3U_FILE, "w", encoding='utf-8') as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        f.write(f'#"name": "Live Sports Events Auto Update"\n')
        f.write(f'#"telegram": "https://t.me/livesportsplay"\n')
        f.write(f'#"last updated": "{update_time} (BD Time)"\n')
        f.write(f'# Total Active Links: {total_channels}\n\n')
        
        for item in all_channels:
            # Group-Title হবে ম্যাচের নাম, আর চ্যানেলের নাম হবে "ম্যাচের নাম - চ্যানেল"
            full_title = f"{item['match_name']} - {item['channel_name']}"
            logo = f"https://placehold.co/800x450/0f172a/ffffff.png?text={item['match_name'].replace(' ', '+')}&font=Oswald"
            
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{item["match_name"]}", {full_title}\n')
            f.write(f"#EXTVLCOPT:http-referrer={item['referrer']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n")
            f.write(f"{item['url']}\n\n")
    
    print(f"\n==================================================")
    print(f"✅ স্ক্যান শেষ! মোট {total_channels} টি কাজ করা লিংক Live_Events.m3u ফাইলে সেভ হয়েছে।")
    print(f"==================================================\n")
