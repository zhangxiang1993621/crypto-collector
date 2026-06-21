"""读取 Supabase Invoice PDF 内容"""

import subprocess
import sys

# 先安装 pdfplumber
try:
    import pdfplumber
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pdfplumber", "-q"])
    import pdfplumber

pdf_path = r"d:\agent-workspace\crypto-collector\Invoice-EZOWCC-00002.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        print(f"\n=== Page {i+1} ===\n")
        text = page.extract_text()
        if text:
            print(text)