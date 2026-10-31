"""Add the pass-B1 catalogue keys to all three language files at once.

The three catalogues must hold an identical key set (I18N-04, checked by
`checkCompleteness()`), so they are edited from one table rather than by hand.
Keys already present are left alone, which makes the script safe to re-run.

Usage:
    python tools/i18n_add_b1.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "web" / "js" / "lang"

#: (key, english, bahasa melayu, 简体中文)
KEYS: list[tuple[str, str, str, str]] = [
    # -- activation gate ---------------------------------------------------- #
    ("activate.oneTime", "A one-time activation for this computer. Nothing is sent anywhere.", "Pengaktifan sekali sahaja untuk komputer ini. Tiada apa dihantar ke mana-mana.", "这台电脑的一次性激活，不会向任何地方发送数据。"),
    ("activate.codeHint", "Upper or lower case both work.", "Huruf besar atau kecil kedua-duanya diterima.", "大小写均可。"),
    ("activate.attemptsLeft", "{count} attempts left.", "{count} percubaan lagi.", "还剩 {count} 次尝试。"),
    ("activate.footerVersion", "Version {version} · offline activation", "Versi {version} · pengaktifan luar talian", "版本 {version} · 离线激活"),
    # -- sign in ------------------------------------------------------------ #
    ("auth.footer", "Version {version} · 100% offline", "Versi {version} · 100% luar talian", "版本 {version} · 100% 离线"),
    ("auth.lockout", "Too many attempts. Try again in {seconds}s.", "Terlalu banyak percubaan. Cuba lagi dalam {seconds}s.", "尝试次数过多，请在 {seconds} 秒后重试。"),
    ("auth.ruleLength", "At least 8 characters", "Sekurang-kurangnya 8 aksara", "至少 8 个字符"),
    ("auth.ruleMatches", "Both new passwords match", "Kedua-dua kata laluan baharu sepadan", "两次输入的新密码一致"),
    ("auth.ruleDiffers", "Different from the current password", "Berbeza daripada kata laluan semasa", "与当前密码不同"),
    ("auth.forced", "Set a new password before you can use IMRI.", "Tetapkan kata laluan baharu sebelum boleh menggunakan IMRI.", "设置新密码后才能使用 IMRI。"),
    ("auth.passwordHint", "At least 8 characters. Avoid your username and obvious words.", "Sekurang-kurangnya 8 aksara. Elakkan nama pengguna dan perkataan jelas.", "至少 8 个字符，避免使用用户名和常见词。"),
    ("auth.passwordChangedBody", "Use your new password next time you sign in.", "Gunakan kata laluan baharu pada log masuk seterusnya.", "下次登录请使用新密码。"),
    ("auth.defaultBannerBody", "Anyone who knows the default password can open this library. Change it now.", "Sesiapa yang tahu kata laluan lalai boleh membuka pustaka ini. Tukar sekarang.", "知道默认密码的人都能打开这个图书馆，请立即修改。"),
    # -- dashboard ---------------------------------------------------------- #
    ("dash.greeting.morning", "Good morning, {name}", "Selamat pagi, {name}", "早上好，{name}"),
    ("dash.greeting.afternoon", "Good afternoon, {name}", "Selamat tengah hari, {name}", "下午好，{name}"),
    ("dash.greeting.evening", "Good evening, {name}", "Selamat petang, {name}", "晚上好，{name}"),
    ("dash.dateLine", "{date} · {school}", "{date} · {school}", "{date} · {school}"),
    ("dash.deltaPct", "{value}% vs the previous 30 days", "{value}% berbanding 30 hari lalu", "较前 30 天 {value}%"),
    ("dash.deltaAbs", "{value} vs the previous 30 days", "{value} berbanding 30 hari lalu", "较前 30 天 {value}"),
    ("dash.alerts.holds", "{count} titles have holds but no copy on the shelf", "{count} judul mempunyai tempahan tetapi tiada naskhah di rak", "{count} 种图书有预约但没有可借复本"),
    ("dash.alerts.limit", "{count} members are at or over their loan limit", "{count} ahli telah mencapai atau melebihi had pinjaman", "{count} 位会员已达或超过借阅上限"),
    ("dash.alerts.fixHolds", "Open reservations", "Buka tempahan", "打开预约"),
    ("dash.alerts.fixLimit", "Open members", "Buka ahli", "打开会员"),
    ("dash.chart.window", "Last 6 months", "6 bulan lepas", "最近 6 个月"),
    ("dash.donutCentre", "titles", "judul", "种"),
    ("dash.col.title", "Title", "Judul", "书名"),
    ("dash.col.member", "Member", "Ahli", "会员"),
    ("dash.col.due", "Due", "Tarikh akhir", "应还"),
    ("dash.col.overdueBy", "{count} d late", "{count} h lewat", "逾期 {count} 天"),
    ("dash.quick.title", "Quick actions", "Tindakan pantas", "快捷操作"),
    ("dash.emptyChart", "No loans recorded in this period.", "Tiada pinjaman direkodkan dalam tempoh ini.", "这段时间没有借阅记录。"),
    ("dash.emptyCollection", "No titles in the catalogue yet.", "Belum ada judul dalam katalog.", "目录里还没有图书。"),
    ("dash.openKpi", "Open {label}", "Buka {label}", "打开{label}"),
    # -- catalogue: list chrome --------------------------------------------- #
    ("cat.count", "{count} titles", "{count} judul", "{count} 种图书"),
    ("cat.countOne", "1 title", "1 judul", "1 种图书"),
    ("cat.sortBy", "Sort by", "Susun ikut", "排序"),
    ("cat.sort.title", "Title (A–Z)", "Judul (A–Z)", "书名（A–Z）"),
    ("cat.sort.author", "Author (A–Z)", "Pengarang (A–Z)", "作者（A–Z）"),
    ("cat.sort.year", "Year (newest)", "Tahun (terbaharu)", "年份（最新）"),
    ("cat.sort.recent", "Recently added", "Baru ditambah", "最近添加"),
    ("cat.filter.availability", "Availability", "Ketersediaan", "可借状态"),
    ("cat.filter.clear", "Clear filters", "Kosongkan penapis", "清除筛选"),
    ("cat.filter.remove", "Remove {label}", "Buang {label}", "移除{label}"),
    ("cat.filter.applied", "Filtered", "Ditapis", "已筛选"),
    ("cat.selected", "{count} selected", "{count} dipilih", "已选 {count} 项"),
    ("cat.bulk.withdraw", "Withdraw", "Tarik balik", "注销"),
    ("cat.bulk.clear", "Clear selection", "Kosongkan pilihan", "取消选择"),
    ("cat.bulk.title", "Withdraw {count} titles?", "Tarik balik {count} judul?", "注销 {count} 种图书？"),
    ("cat.bulk.body", "Copies on the shelf are marked withdrawn. Copies on loan keep their loan.", "Naskhah di rak ditanda ditarik balik. Naskhah dipinjam kekal.", "在架复本将标记为已注销，借出中的复本保持不变。"),
    ("cat.bulk.done", "{count} titles updated", "{count} judul dikemas kini", "已更新 {count} 种图书"),
    ("cat.bulk.none", "Nothing was changed.", "Tiada perubahan.", "没有更改。"),
    ("cat.empty.search.title", "No matches for “{term}”", "Tiada padanan untuk “{term}”", "没有匹配“{term}”的结果"),
    ("cat.empty.search.body", "Try a different word, or clear the filters.", "Cuba perkataan lain, atau kosongkan penapis.", "换个关键词，或清除筛选。"),
    ("cat.empty.addFirst", "Add your first book", "Tambah buku pertama", "添加第一本图书"),
    ("cat.empty.importAction", "Import a file", "Import fail", "导入文件"),
    ("cat.col.type", "Type", "Jenis", "类型"),
    ("cat.export.done", "{count} rows exported", "{count} baris dieksport", "已导出 {count} 行"),
    ("cat.export.empty", "There is nothing to export.", "Tiada apa untuk dieksport.", "没有可导出的内容。"),
    ("cat.export.working", "Preparing the export…", "Menyediakan eksport…", "正在准备导出…"),
    # -- catalogue: import wizard ------------------------------------------- #
    ("cat.import.title", "Import books", "Import buku", "导入图书"),
    ("cat.import.step.pick", "Choose a file", "Pilih fail", "选择文件"),
    ("cat.import.step.check", "Check the rows", "Semak baris", "检查数据"),
    ("cat.import.step.commit", "Import", "Import", "导入"),
    ("cat.import.drop", "Drop a CSV or Excel file here", "Lepaskan fail CSV atau Excel di sini", "将 CSV 或 Excel 文件拖到这里"),
    ("cat.import.browse", "Choose a file", "Pilih fail", "选择文件"),
    ("cat.import.formats", "CSV or XLSX. The first row must hold the column names.", "CSV atau XLSX. Baris pertama mesti mengandungi nama lajur.", "CSV 或 XLSX，第一行必须是列名。"),
    ("cat.import.reading", "Reading the file…", "Membaca fail…", "正在读取文件…"),
    ("cat.import.ready", "{count} rows ready", "{count} baris sedia", "{count} 行可导入"),
    ("cat.import.rejected", "{count} rows skipped", "{count} baris dilangkau", "{count} 行已跳过"),
    ("cat.import.total", "{count} rows in the file", "{count} baris dalam fail", "文件共 {count} 行"),
    ("cat.import.readyTable", "Rows that will be imported", "Baris yang akan diimport", "将导入的行"),
    ("cat.import.rejectedTable", "Rows that will be skipped", "Baris yang akan dilangkau", "将跳过的行"),
    ("cat.import.rowNo", "Row", "Baris", "行"),
    ("cat.import.reason", "Reason", "Sebab", "原因"),
    ("cat.import.more", "{count} more not shown", "{count} lagi tidak dipaparkan", "另有 {count} 条未显示"),
    ("cat.import.commit", "Import {count} rows", "Import {count} baris", "导入 {count} 行"),
    ("cat.import.committing", "Importing…", "Mengimport…", "正在导入…"),
    ("cat.import.done", "Import finished", "Import selesai", "导入完成"),
    ("cat.import.result", "{imported} imported, {rejected} skipped", "{imported} diimport, {rejected} dilangkau", "已导入 {imported} 行，跳过 {rejected} 行"),
    ("cat.import.downloadRejected", "Download the skipped rows", "Muat turun baris yang dilangkau", "下载被跳过的行"),
    ("cat.import.another", "Import another file", "Import fail lain", "导入另一个文件"),
    ("cat.import.expired", "That preview has expired. Choose the file again.", "Pratonton itu telah tamat tempoh. Pilih fail sekali lagi.", "预览已过期，请重新选择文件。"),
    ("cat.import.noRows", "No row in that file passed validation.", "Tiada baris dalam fail itu lulus pengesahan.", "该文件没有通过校验的行。"),
    ("cat.import.cancelled", "Import cancelled.", "Import dibatalkan.", "导入已取消。"),
    ("cat.import.file", "File", "Fail", "文件"),
    # -- catalogue: detail drawer ------------------------------------------- #
    ("cat.detail.isbn", "ISBN-13", "ISBN-13", "ISBN-13"),
    ("cat.detail.publisher", "Publisher", "Penerbit", "出版社"),
    ("cat.detail.year", "Year", "Tahun", "年份"),
    ("cat.detail.edition", "Edition", "Edisi", "版次"),
    ("cat.detail.language", "Language", "Bahasa", "语言"),
    ("cat.detail.dewey", "DDC", "DDC", "DDC"),
    ("cat.detail.callNo", "Call no.", "No. panggilan", "索书号"),
    ("cat.detail.material", "Material type", "Jenis bahan", "资料类型"),
    ("cat.detail.accession", "Accession", "Nombor perolehan", "登录号"),
    ("cat.detail.status", "Status", "Status", "状态"),
    ("cat.detail.copiesCount", "Copies", "Naskhah", "复本"),
    ("cat.detail.subjects", "Subjects", "Subjek", "主题"),
    ("cat.detail.languages", "Languages", "Bahasa", "语言"),
    ("cat.detail.noSubjects", "No subject headings recorded.", "Tiada tajuk subjek direkodkan.", "没有记录主题词。"),
    ("cat.detail.tabCurrent", "On loan now", "Sedang dipinjam", "在借"),
    ("cat.detail.noCurrent", "No copy is out.", "Tiada naskhah dipinjam.", "没有借出的复本。"),
    ("cat.detail.printLabel", "Print label", "Cetak label", "打印标签"),
    ("cat.detail.marcRaw", "View raw JSON", "Lihat JSON mentah", "查看原始 JSON"),
    ("cat.detail.marcHide", "Hide raw JSON", "Sembunyi JSON mentah", "隐藏原始 JSON"),
    ("cat.detail.copyMenu", "Copy actions", "Tindakan naskhah", "复本操作"),
    ("cat.detail.labelTitle", "Label preview", "Pratonton label", "标签预览"),
    ("cat.detail.labelHint", "This is what the sticker will look like.", "Beginilah rupa pelekat itu.", "贴纸将长这样。"),
    ("cat.detail.print", "Print", "Cetak", "打印"),
    ("cat.detail.openFull", "Open the full record", "Buka rekod penuh", "打开完整记录"),
    ("cat.copy.editTitle", "Edit copy", "Sunting naskhah", "编辑复本"),
    ("cat.copy.markLost", "Mark lost", "Tanda hilang", "标记遗失"),
    ("cat.copy.markLostTitle", "Mark this copy lost?", "Tanda naskhah ini hilang?", "将此复本标记为遗失？"),
    ("cat.copy.markLostBody", "It stops being lendable and the title status is recalculated.", "Ia tidak lagi boleh dipinjam dan status judul dikira semula.", "该复本将不可借，图书状态会重新计算。"),
    ("cat.copy.deleteTitle", "Delete this copy?", "Padam naskhah ini?", "删除此复本？"),
    ("cat.copy.deleteBody", "Barcode {barcode} will be removed.", "Kod bar {barcode} akan dibuang.", "条码 {barcode} 将被删除。"),
    ("cat.copy.onLoan", "This copy is out on loan. Take it back first.", "Naskhah ini sedang dipinjam. Pulangkan dahulu.", "该复本已借出，请先归还。"),
    ("cat.copy.lastCopy", "A title must keep at least one copy.", "Sesuatu judul mesti menyimpan sekurang-kurangnya satu naskhah.", "每种图书至少保留一个复本。"),
    ("cat.copy.note", "Note", "Nota", "备注"),
    ("cat.copy.saved", "Copy updated", "Naskhah dikemas kini", "复本已更新"),
    ("cat.label.none", "This title has no copy to label yet.", "Judul ini belum ada naskhah untuk dilabel.", "该图书还没有可打印标签的复本。"),
    # -- catalogue: editor -------------------------------------------------- #
    ("cat.form.edition", "Edition", "Edisi", "版次"),
    ("cat.form.scheme", "Classification scheme", "Skema pengelasan", "分类法"),
    ("cat.form.isbnHint", "ISBN-10 or ISBN-13. It is stored as ISBN-13.", "ISBN-10 atau ISBN-13. Disimpan sebagai ISBN-13.", "ISBN-10 或 ISBN-13，将以 ISBN-13 保存。"),
    ("cat.form.isbnNormalised", "Stored as {isbn}", "Disimpan sebagai {isbn}", "将保存为 {isbn}"),
    ("cat.form.deweyHint", "Digits, optionally with a decimal point.", "Digit, dengan titik perpuluhan jika perlu.", "数字，可带小数点。"),
    ("cat.form.callNoHint", "Shelving code, e.g. 823.914 ORW.", "Kod rak, cth. 823.914 ORW.", "排架号，例如 823.914 ORW。"),
    ("cat.form.duplicates", "Similar titles already here", "Judul serupa sudah ada", "已有相似图书"),
    ("cat.form.section.publication", "Publication", "Penerbitan", "出版信息"),
    ("cat.form.copiesHint", "Copies are created together with the title.", "Naskhah dicipta bersama judul.", "复本随图书一起创建。"),
    ("cat.created", "Book created", "Buku dicipta", "图书已创建"),
    ("cat.updated", "Book updated", "Buku dikemas kini", "图书已更新"),
    ("cat.deleted", "Book deleted", "Buku dipadam", "图书已删除"),
    # -- language vocabulary ------------------------------------------------ #
    # Proper names, so the same string in every catalogue; `lang.en/ms/zh`
    # already followed this rule.
    ("lang.ta", "தமிழ்", "தமிழ்", "தமிழ்"),
    ("lang.ar", "العربية", "العربية", "العربية"),
    ("lang.fr", "Français", "Français", "Français"),
    ("lang.de", "Deutsch", "Deutsch", "Deutsch"),
    ("lang.es", "Español", "Español", "Español"),
    ("lang.id", "Bahasa Indonesia", "Bahasa Indonesia", "Bahasa Indonesia"),
    ("lang.ja", "日本語", "日本語", "日本語"),
    ("lang.ko", "한국어", "한국어", "한국어"),
    ("lang.other", "Other", "Lain-lain", "其他"),
]

#: Header comment inserted above the new block.
BANNER = "  /* ---- pass B1: auth gate, dashboard, catalogue -------------------------- */\n"


def parse_keys(text: str) -> set[str]:
    """Return the top-level keys already present in a catalogue file."""
    return set(re.findall(r"^  '([^']+)':", text, re.M))


def build_block(rows: list[tuple[str, str]], indent: str = "  ") -> str:
    """Render the new key lines."""
    out = []
    for key, value in rows:
        if "'" in value:
            raise SystemExit("value for %r contains an apostrophe; use a curly one" % key)
        out.append("%s'%s': '%s',\n" % (indent, key, value))
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = {"en": LANG_DIR / "en.js", "ms": LANG_DIR / "ms.js", "zh": LANG_DIR / "zh.js"}
    sources = {code: path.read_text(encoding="utf-8") for code, path in files.items()}

    existing = parse_keys(sources["en"])
    fresh = [row for row in KEYS if row[0] not in existing]
    skipped = [row[0] for row in KEYS if row[0] in existing]

    # Every key must be new in *all three* files, or the sets diverge.
    for code, text in sources.items():
        present = parse_keys(text)
        for row in fresh:
            if row[0] in present:
                print("!! %s already has %s but en.js does not" % (code, row[0]), file=sys.stderr)
                return 1

    if not fresh:
        print("nothing to add; %d keys already present" % len(skipped))
        return 0

    for index, (code, path) in enumerate(files.items()):
        text = sources[code]
        column = 1 if code == "en" else 2 if code == "ms" else 3
        rows = [(row[0], row[column]) for row in fresh]
        block = BANNER + build_block(rows)
        marker = text.rstrip()
        if not marker.endswith("};"):
            print("!! %s does not end with `};`" % path, file=sys.stderr)
            return 1
        updated = marker[: -len("};")] + block + "};\n"
        if args.dry_run:
            print("would add %d keys to %s" % (len(rows), path.name))
        else:
            path.write_text(updated, encoding="utf-8")
        del index

    print("added %d keys to en.js, ms.js and zh.js" % len(fresh))
    if skipped:
        print("already present: %d" % len(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
