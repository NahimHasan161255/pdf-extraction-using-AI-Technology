import json
import re
import pdfplumber
import os
import glob

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.lib.styles import getSampleStyleSheet
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
except ImportError:
    pass

def generate_pdf(data, output_file):
    doc = SimpleDocTemplate(output_file, pagesize=landscape(A4))
    elements = []
    styles = getSampleStyleSheet()
    
    if not data:
        return
        
    styleN = styles['Normal']
    styleN.fontName = 'HeiseiKakuGo-W5'
    
    # 汎用的な列の順序と日本語ヘッダーの定義
    column_def = [
        ("design_code", "符号"),
        ("floor", "階"),
        ("steel_standard", "鋼材規格"),
        ("steel_type", "鋼材種類"),
        ("steel_size", "鋼材サイズ"),
        ("length", "長さ"),
        ("quantity", "台数"),
        ("usage_count", "使用数"),
        ("remarks", "備考")
    ]
    
    # データセットに実際に存在するキーを特定
    existing_keys = set()
    for item in data:
        existing_keys.update(item.keys())
        
    # 存在するキーのみを抽出し、定義された順序で動的ヘッダーを作成
    active_cols = [col for col in column_def if col[0] in existing_keys]
    
    # 未知のキー（column_defに定義されていない新しい列）があれば末尾に追加
    known_keys = {col[0] for col in column_def}
    unknown_keys = existing_keys - known_keys
    for uk in sorted(unknown_keys):
        active_cols.append((uk, uk.capitalize()))  # フォールバックとしてキー名をそのままヘッダーにする
    
    headers = [col[1] for col in active_cols]
    table_data = [headers]
    
    # 動的に行データを生成
    for item in data:
        row = []
        for key, _ in active_cols:
            val = item.get(key, "")
            # PDFの見た目のため、全断面の場合はセルを空白にする
            if key == "remarks" and val == "全断面":
                val = ""
            row.append(str(val))
        table_data.append(row)
            
    t = Table(table_data)
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'HeiseiKakuGo-W5'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    
    elements.append(t)
    doc.build(elements)

def is_steel_size(text):
    if not text: return False
    text = str(text).strip()
    return bool(re.search(r'\d+x\d+x\d+', text))

def extract_unified(table):
    """
    ルールベースの空間レイアウトAIパーサー。
    ハードコードされた列インデックスではなく、2次元グリッドの関係性（上/左のセマンティックバインディング）を使用してデータを抽出します。
    あらゆる表レイアウトに対応します。
    """
    extracted = []
    
    # 結合されたヘッダーを処理するため、空のセルを前方の値で埋める（ヘッダー行 r < 2 のみ適用）
    filled_table = [list(row) for row in table]
    for r in range(min(2, len(filled_table))):
        last_val = ""
        for c in range(len(filled_table[r])):
            val = filled_table[r][c]
            if val and str(val).strip():
                last_val = str(val).strip()
            filled_table[r][c] = last_val
            
    def find_header_up(r, c):
        for i in range(r - 1, -1, -1):
            val = str(filled_table[i][c] or "").strip()
            if val and not is_steel_size(val): return val
        return None

    def find_header_left(r, c):
        for i in range(c - 1, -1, -1):
            val = str(filled_table[r][i] or "").strip()
            if val and not is_steel_size(val): return val
        return None

    for r in range(len(table)):
        row = table[r]
        for c in range(len(row)):
            cell = row[c]
            if cell and is_steel_size(cell):
                mat = {"steel_size": str(cell).strip()}
                
                # 空間関係の推論（上方向のヘッダーを取得）
                header_up = find_header_up(r, c)
                
                # 行全体を横方向にスキャンしてプロパティ（符号、階）を検索
                row_design_code = None
                floor_candidate = None
                for c_scan in range(c):
                    val = str(filled_table[r][c_scan] or "").strip()
                    if re.search(r'G\d', val) or re.search(r'\dG', val):
                        row_design_code = val
                    elif val and "階" not in val and not is_steel_size(val) and val not in ["H", "SS400", "全断面", "備考"]:
                        if not floor_candidate:
                            floor_candidate = val
                            
                # コンテキストに応じたバインディング
                if header_up and (re.search(r'G\d', header_up) or re.search(r'\dG', header_up)):
                    mat["design_code"] = header_up
                    if floor_candidate: mat["floor"] = floor_candidate
                elif row_design_code:
                    mat["design_code"] = row_design_code
                
                # 列を下方向にスキャンして特定の備考を検索（List 1の「ピン接合」など、最大3行分）
                col_remark = None
                for r_scan in range(r + 1, min(r + 4, len(filled_table))):
                    val = str(filled_table[r_scan][c] or "").strip()
                    if is_steel_size(val): break
                    if len(val) > 2 and ("TYPE" in val or "ピン" in val or "PIN" in val or "全断面" in val):
                        col_remark = val
                
                # すぐ左のセルにある一般的な備考を検索
                left_cell = str(filled_table[r][c-1] or "").strip() if c > 0 else ""
                row_remark = left_cell if ("全断面" in left_cell or "TYPE" in left_cell) else None
                
                # 数値メトリクス、鋼材規格、鋼材種類を抽出
                for c_scan in range(len(row)):
                    if c_scan == c: continue
                    val = str(row[c_scan] or "").strip()
                    if not val: continue
                    
                    if val == "H" or val == "B":
                        mat["steel_type"] = val
                    elif val.isdigit() and val != floor_candidate:
                        num = int(val)
                        if num > 500: mat["length"] = num
                        elif num < 100:
                            if "quantity" not in mat: mat["quantity"] = num
                            else: mat["usage_count"] = num
                    elif "SS" in val or "SN" in val:
                        mat["steel_standard"] = val
                    elif not row_remark and ("全断面" in val or "TYPE" in val or "PIN" in val or "ピン" in val):
                        row_remark = val
                        
                if col_remark:
                    mat["remarks"] = col_remark
                elif row_remark:
                    mat["remarks"] = row_remark
                        
                extracted.append(mat)
                
    return extracted

def main():
    # ディレクトリ内のすべてのPDFファイルを動的に取得 (課題.pdfなどは除外)
    pdf_files = [f for f in glob.glob("*.pdf") if "課題" not in f and "Task" not in f and not f.endswith("_output.pdf")]
    
    for pdf_file in pdf_files:
        try:
            all_results = []
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table: continue
                        data = extract_unified(table)
                        for item in data:
                            if item not in all_results:
                                all_results.append(item)
            
            # 抽出した結果を1列目（design_code）でソート
            all_results.sort(key=lambda x: str(x.get("design_code", "")))
            
            # JSON出力
            json_file = pdf_file.replace(".pdf", "_output.json")
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)
                
            # PDF出力
            pdf_out_file = pdf_file.replace(".pdf", "_output.pdf")
            try:
                generate_pdf(all_results, pdf_out_file)
                print(f"Generated {pdf_out_file}")
            except Exception as e:
                print(f"Failed to generate PDF for {pdf_file}: {e}")
                
        except Exception as e:
            pass

if __name__ == "__main__":
    main()
