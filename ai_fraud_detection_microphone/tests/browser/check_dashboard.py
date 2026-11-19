"""Optional real-browser smoke test. Run with a Playwright-enabled Python env."""
import re
import csv
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[2]
ARTIFACTS=ROOT/'tests/artifacts';ARTIFACTS.mkdir(exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url=f'http://127.0.0.1:{port}/dashboard.html'
with (ARTIFACTS/'dashboard_server.log').open('w') as log:
    server=subprocess.Popen([str(ROOT.parent/'.env_laya/bin/python'),str(ROOT/'backend/app/main.py'),'--dashboard','--port',str(port)],stdout=log,stderr=log)
    try:
        until=time.monotonic()+15
        while True:
            try:
                with urlopen(url,timeout=.5) as response:
                    assert response.status==200
                break
            except OSError:
                if time.monotonic()>until:raise
                time.sleep(.1)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url);expect(page.locator('#state')).to_have_text('Siap')
            page.screenshot(path=str(ARTIFACTS/'dashboard_desktop.png'),full_page=True)
            page.select_option('#source','sample');page.select_option('#privacy','omit')
            page.click('#start')
            expect(page.locator('#state')).to_have_text(re.compile('^(Memuat model|Berjalan)$'))
            page.reload()
            expect(page.locator('#state')).to_have_text(re.compile('^(Memuat model|Berjalan|Selesai)$'))
            expect(page.locator('#state')).to_have_text('Selesai',timeout=90000)
            expect(page.locator('#decisions')).not_to_have_text('0')
            assert page.locator('.item').count()>0
            assert page.locator('.item').first.inner_text().find('[TRANSCRIPT_OMITTED]')>=0
            with page.expect_download() as download:
                page.click('#download')
            csv_path=ARTIFACTS/'dashboard_download.csv';download.value.save_as(csv_path)
            with csv_path.open(encoding='utf-8-sig',newline='') as file:rows=list(csv.DictReader(file))
            assert len(rows)==int(page.locator('#decisions').inner_text())
            page.screenshot(path=str(ARTIFACTS/'dashboard_results.png'),full_page=True)
            page.select_option('#source','upload')
            page.set_input_files('#recording',{'name':'invalid.wav','mimeType':'audio/wav','buffer':b'not audio'})
            page.click('#upload')
            expect(page.locator('#notice')).to_contain_text('tidak dapat dibaca')
            expect(page.locator('#start')).to_be_disabled()
            page.set_input_files('#recording',str(ROOT.parent/'legacy/test_output_new.wav'))
            page.click('#upload')
            expect(page.locator('#uploadInfo')).to_contain_text('siap diuji',timeout=30000)
            page.screenshot(path=str(ARTIFACTS/'dashboard_upload.png'),full_page=True)
            page.click('#start')
            expect(page.locator('#state')).to_have_text(re.compile('^(Memuat model|Berjalan)$'))
            page.reload()
            expect(page.locator('#state')).to_have_text('Selesai',timeout=90000)
            expect(page.locator('#decisions')).not_to_have_text('0')
            expect(page.locator('#uploadInfo')).to_contain_text('test_output_new.wav')
            with page.expect_download() as uploaded_download:
                page.click('#download')
            uploaded_download.value.save_as(ARTIFACTS/'dashboard_upload_download.csv')
            page.screenshot(path=str(ARTIFACTS/'dashboard_upload_results.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(ARTIFACTS/'dashboard_mobile.png'),full_page=True)
            page.click('#start');expect(page.locator('#stop')).to_be_enabled()
            page.click('#stop')
            expect(page.locator('#state')).to_have_text('Selesai',timeout=45000)
            assert not errors,errors
            browser.close()
            print('Browser QA passed: desktop/mobile, reload, real GPU sample/upload, invalid upload, CSV download, stop; no JS errors.')
    finally:
        server.terminate()
        try:server.wait(timeout=45)
        except subprocess.TimeoutExpired:server.kill();server.wait()
