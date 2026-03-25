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
        result = subprocess.run(command, capture_output=True, shell=True, timeout=15)
        return result.stdout.decode('utf-8', errors='ignore')
    except Exception as e:
        return None

def clean_channel_name(name):
    name = re.sub(r'(\s*Live Stream(ing)?|\s*-\s*CricHD|\s*US\s*-|\s*-\s*Free|\s*Watch|\s*HD|\s*-\s*PSL T20 On|\s*Play\s*-\s*01)', '', name, flags=re.IGNORECASE)
    return " ".join(name.split())

# --- ১. হোমপেজ থেকে লাইভ ম্যাচ খোঁজার ফাংশন ---
def get_live_matches():
    logging.info(f"\n[*] হোমপেজ থেকে লাইভ ম্যাচ স্ক্যান করা হচ্ছে...")
    html = run_command(f"curl -sL {CRICHD_BASE_URL}")
    if not html: return []
    
    matches = []
    seen = set()
    
    pattern = r'<a\s+href=[\'"](https://crichd\.com\.co/[^\'"]+)[\'"][^>]*itemprop=[\'"]url[\'"]>\s*<h2[^>]*>(.*?)</h2>'
    found_matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
    
    for url, title in found_matches:
        url = url.strip()
        title = re.sub(r'<[^>]+>', '', title).strip()
        
        if url not in seen and len(title) > 3:
            matches.append((url, clean_channel_name(title)))
            seen.add(url)
                    
    logging.info(f"[+] মোট {len(matches)} টি লাইভ ইভেন্ট পাওয়া গেছে!")
    return matches

# --- ৩. কোনো টেস্টিং ছাড়াই নিখুঁত ডোমেইন ও টোকেন এক্সট্রাক্টর ---
def extract_bhalocast_m3u8(player_url):
    try:
        player_id = player_url.split("id=")[1].split("&")[0]
        playerado_url = f"https://playerado.top/embed2.php?id={player_id}"
        
        embed_page_content = run_command(f"curl -sL '{playerado_url}'")
        if not embed_page_content: return None

        fid = re.search(r'fid\s*=\s*\"([^\"]+)\"', embed_page_content).group(1)
        v_con = re.search(r'v_con\s*=\s*\"([^\"]+)\"', embed_page_content).group(1)
        v_dt = re.search(r'v_dt\s*=\s*\"([^\"]+)\"', embed_page_content).group(1)

        atplay_url = f"https://player0003.com/atplay.php?v={fid}&hello={v_con}&expires={v_dt}"
        atplay_page_content = run_command(f"curl -siL --user-agent \"Mozilla/5.0\" --referer \"https://playerado.top/\" '{atplay_url}'")
        if not atplay_page_content: return None

        func_name = re.search(r'player\.load\({source: (\w+)\(\),', atplay_page_content).group(1)
        func_body = re.search(r'function\s+' + func_name + r'\s*\(\)\s*{(.*?)}', atplay_page_content, re.DOTALL).group(1)

        md5_var = re.search(r'url \+= "\?md5="\s*\+\s*(\w+);', func_body).group(1)
        expires_var = re.search(r'url \+= "&expires="\s*\+\s*(\w+);', func_body).group(1)
        s_var = re.search(r'url \+= "&s="\s*\+\s*(\w+);', func_body).group(1)

        md5 = re.search(r'var ' + md5_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)
        expires = re.search(r'var ' + expires_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)
        s_val = re.search(r'var ' + s_var + r'\s*=\s*\"(.*?)\"', atplay_page_content).group(1)

        # ম্যাজিক: কোনো টেস্টিং ছাড়াই সরাসরি JS থেকে আসল ডোমেইন পড়া
        base_url_var = re.search(r'var url = (\w+);', func_body).group(1)
        constructor_string = re.search(r'var ' + base_url_var + r'\s*=\s*(.*?);', atplay_page_content).group(1)
        real_base_url_var = constructor_string.split('+')[0].strip()
        
        base_url_str_with_plus = re.search(r"var " + real_base_url_var + r" = (.*?);", atplay_page_content).group(1)
        js_string_parts = re.findall(r"['\"](.*?)['\"]", base_url_str_with_plus)
        base_url = "".join(js_string_parts)
        
        if not base_url.startswith("http"):
            return None

        stream_path = f"/hls/{fid}.m3u8"
        final_url = f"{base_url}{stream_path}?md5={md5}&expires={expires}&ch={fid}&s={s_val}"
        return final_url
    except Exception as e:
        return None

# --- ২. ম্যাচের ভেতরে ঢুকে চ্যানেল স্ক্যান করার ফাংশন ---
def get_match_streams(match_url, match_title):
    logging.info(f"\n➤ স্ক্যান করা হচ্ছে ম্যাচ: {match_title}")
    html = run_command(f"curl -sL '{match_url}'")
    if not html: return []

    streams = []
    rows = re.split(r'(?i)<tr', html)
    
    for row in rows:
        link_match = re.search(r'href=[\'"](https://(?:player\.)?dadocric\.st/player\.php\?id=[^\'"]+)[\'"]', row, re.IGNORECASE)
        if link_match:
            player_url = link_match.group(1)
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
            channel_name = "Event Channel"
            
            if tds:
                clean_name = re.sub(r'<[^>]+>', '', tds[0]).strip()
                if clean_name and "Channel Name" not in clean_name:
                    channel_name = clean_name
                    
            logging.info(f"   - চ্যানেল পাওয়া গেছে: {channel_name}, ডিক্রিপ্ট করা হচ্ছে...")
            stream_link = extract_bhalocast_m3u8(player_url)
            
            if stream_link:
                # ডোমেইনটা লগে প্রিন্ট করে দেখাচ্ছে যে কোনটা পেল
                domain_name = stream_link.split('/hls')[0]
                logging.info(f"     ✅ লিংক সফলভাবে উদ্ধার হয়েছে! ({domain_name})")
                streams.append((channel_name, stream_link))
            else:
                logging.info(f"     ❌ লিংক উদ্ধারে ব্যর্থ।")
                
    if not streams:
        logging.info("   [-] এই ম্যাচে কোনো লিংক পাওয়া যায়নি।")
        
    return streams

# --- Main Execution ---
if __name__ == "__main__":
    all_channels = []
    live_matches = get_live_matches()
    
    for match_url, match_title in live_matches:
        channels = get_match_streams(match_url, match_title)
        for ch_name, stream_url in channels:
            all_channels.append({
                'match_name': match_title,
                'channel_name': ch_name,
                'url': stream_url,
                'referrer': "https://player0003.com/"
            })
        time.sleep(1)
        
    total_channels = len(all_channels)
    
    update_time = ""
    if ZoneInfo:
        try:
            dhaka_tz = ZoneInfo('Asia/Dhaka')
            update_time = datetime.datetime.now(dhaka_tz).strftime('%Y-%m-%d %I:%M:%S %p')
        except Exception:
            update_time = datetime.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
            
    with open(OUTPUT_M3U_FILE, "w", encoding='utf-8') as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        f.write(f'#"name": "Live Sports Events Auto Update"\n')
        f.write(f'#"telegram": "https://t.me/livesportsplay"\n')
        f.write(f'#"last updated": "{update_time} (BD Time)"\n')
        f.write(f'# Total Active Links: {total_channels}\n\n')
        
        for item in all_channels:
            full_title = f"{item['match_name']} - {item['channel_name']}"
            logo = f"https://placehold.co/800x450/0f172a/ffffff.png?text={item['match_name'].replace(' ', '+')}&font=Oswald"
            
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{item["match_name"]}", {full_title}\n')
            f.write(f"#EXTVLCOPT:http-referrer={item['referrer']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n")
            f.write(f"{item['url']}\n\n")
    
    print(f"\n==================================================")
    print(f"✅ স্ক্যান শেষ! মোট {total_channels} টি কাজ করা লিংক Live_Events.m3u ফাইলে সেভ হয়েছে।")
    print(f"==================================================\n")
