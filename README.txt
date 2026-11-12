REDDIT MEDIA DOWNLOADER 3.6

INSTALL / RUN
1. Install Python 3.10+ and Google Chrome.
2. Extract this folder.
3. Double-click Run_Reddit_Downloader.bat.
   It installs the Python requirements automatically on first launch.

CHROMEDRIVER
- ChromeDriver is NOT bundled. This avoids shipping a potentially mismatched driver.
- Recommended: leave the ChromeDriver field empty. Selenium Manager (Selenium 4.6+)
  will locate Chrome and obtain/manage a compatible driver automatically.
- If automatic management fails, download the ChromeDriver matching your installed
  Chrome version and select chromedriver.exe in Settings.
- ChromeDriver downloads/management require internet access.
- Chrome opens as a separate visible Chrome window. The app uses its own
  ./chrome_profile folder. Sign in there yourself if Reddit requires your session.
- It does not copy your normal Chrome profile or extract your credentials.

FEATURES
- Separate visible Chrome browser controlled by Selenium.
- Public subreddit scanning with configurable post and scroll limits.
- Extracts post title, author, community, permalink and verified media URLs.
- Keeps post_url separate from media_url. Reddit preview thumbnails are not queued
  as original media.
- Direct file downloads use streamed HTTP requests; Reddit posts / external
  extractor URLs use yt-dlp. HLS playlists use yt-dlp.
- Parallel queue, retry failed, CSV/JSON import, history, ZIP and manifest export.
- Existing download_history.json and subreddit_index.json are retained.
- Legacy 3.5 index entries where media_url equals post_url are treated as extractor
  jobs, not direct media downloads.

IMPORTANT
- Scraping is limited to content Reddit makes publicly visible to the browser.
- If Reddit presents a CAPTCHA, access-denied page, login gate, or rate limit,
  stop and resolve it normally. The application does not bypass these controls.
- Selenium Manager may need internet access the first time it runs.
- This source package has not been tested on your specific PC or every Reddit layout.
