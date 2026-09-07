"""提交任务, 跑完后从 worker_log.txt 提取 P4-A1 详情."""
import httpx
import time
import sys
import glob
import os

# Windows 路径用 os.path.join
FIXTURES = os.path.join('tests', 'fixtures')
manifest_path = os.path.join(FIXTURES, 'photo2.jpg')  # 清单图

# 所有修箱照片: 20783951000000x.jpg
photos_paths = sorted(
    glob.glob(os.path.join(FIXTURES, '2078395100*.jpg'))
)
print(f'清单图: {manifest_path}')
print(f'找到 {len(photos_paths)} 张照片: {[os.path.basename(p) for p in photos_paths]}')

manifest = open(manifest_path, 'rb').read()
files = [('manifest_image', ('manifest.jpg', manifest, 'image/jpeg'))]
for p in photos_paths:
    files.append(('photos', (os.path.basename(p), open(p, 'rb').read(), 'image/jpeg')))

r = httpx.post('http://localhost:8000/api/v1/audits', files=files, data={}, timeout=30)
tid = r.json()['task_id']
print(f'SUBMIT: {tid}')

# 轮询
for _ in range(40):
    time.sleep(8)
    r = httpx.get(f'http://localhost:8000/api/v1/audits/{tid}', timeout=5)
    d = r.json()
    status = d['status']
    stage = d['current_stage']
    print(f'[{time.strftime("%H:%M:%S")}] status={status} stage={stage}')
    if status in ('succeeded', 'failed'):
        break

print('=' * 60)
print('container:', d.get('container_number'))
print('recommendation:', d.get('final_recommendation'))
if d.get('error'):
    print('error:', d['error']['code'], '-', d['error']['message'][:300])
    sys.exit()

# 等待日志写入
time.sleep(2)
print('-' * 60)
print('P4-A1 详细日志 (按 item_no 分组):')
try:
    with open('worker_log.txt', 'r', encoding='utf-8', errors='ignore') as f:
        lines = [l for l in f if 'p4a1_detail' in l]
    # 按 item_no 排序
    import re
    def item_no(l):
        m = re.search(r"item_no=(\d+)", l)
        return int(m.group(1)) if m else 99
    lines.sort(key=item_no)
    for l in lines:
        print(l.rstrip())
except Exception as e:
    print(f'读取日志失败: {e}')
