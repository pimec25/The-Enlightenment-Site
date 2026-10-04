"""Read only analysis columns using a bounded row-at-a-time XML parser.

Workbook metadata uses pinned openpyxl 3.1.5; worksheet XML uses lxml's
filtered end-row events so unrelated cell values are never decoded.
"""
from lxml import etree
from openpyxl.utils.datetime import from_excel, from_ISO8601
from openpyxl.utils.cell import column_index_from_string

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
COLUMNS = {'Request Date', 'Extended Price', 'Extended Cost', 'Parent Number',
           '2nd Item Number', 'Value Stream Product Family', 'Order Number',
           'Line Number', 'Customer Name', 'Product Name'}


class ProjectedSheet:
    def __init__(self, book):
        self.book = book
        self.sheet = book.active
        self.max_row = self.sheet.max_row
        self.title = self.sheet.title

    def iter_rows(self, values_only=True):
        selected = None
        width = self.sheet.max_column or 1
        if width > 16384 or (self.max_row or 1) > 1048576:
            raise ValueError('Worksheet dimensions exceed Excel limits.')
        expected = 1
        with self.book._archive.open(self.sheet._worksheet_path) as source:
            for _, row in etree.iterparse(source, events=('end',), tag=NS+'row',
                                         resolve_entities=False, no_network=True, load_dtd=False):
                row_number = int(row.get('r', expected))
                if not expected <= row_number <= 1048576:
                    raise ValueError('Worksheet row references are invalid.')
                while expected < row_number:
                    yield (None,) * width
                    expected += 1
                values = [None] * width
                for cell in row:
                    if cell.tag != NS+'c':
                        continue
                    letters = cell.get('r', '').rstrip('0123456789')
                    if selected is not None and letters not in selected:
                        continue
                    index = column_index_from_string(letters) - 1
                    if not 0 <= index < 16384:
                        raise ValueError('Worksheet column references are invalid.')
                    if index >= len(values):
                        values.extend([None] * (index+1-len(values)))
                    kind = cell.get('t', 'n')
                    value = cell.findtext(NS+'v')
                    if kind == 'inlineStr':
                        inline = cell.find(NS+'is')
                        value = ''.join((n.text or '') if n.tag == NS+'t' else
                                        ''.join(t.text or '' for t in n.findall(NS+'t')) if n.tag == NS+'r' else ''
                                        for n in inline) if inline is not None else None
                    elif value is not None:
                        if kind == 's':
                            value = self.sheet._shared_strings[int(value)]
                        elif kind == 'n':
                            value = float(value) if any(c in value for c in '.Ee') else int(value)
                            style = int(cell.get('s', '0'))
                            if style in self.book._date_formats:
                                value = from_excel(value, self.book.epoch,
                                                   timedelta=style in self.book._timedelta_formats)
                        elif kind == 'b':
                            value = bool(int(value))
                        elif kind == 'd':
                            value = from_ISO8601(value)
                    values[index] = value
                if selected is None:
                    from openpyxl.utils.cell import get_column_letter
                    selected = {get_column_letter(i+1) for i, v in enumerate(values) if v in COLUMNS}
                    width = max((i+1 for i, v in enumerate(values) if v in COLUMNS), default=1)
                row.clear()
                while row.getprevious() is not None:
                    del row.getparent()[0]
                yield tuple(values)
                expected = row_number + 1


class ProjectedBook:
    def __init__(self, book):
        self.active = ProjectedSheet(book)

    def close(self):
        pass  # The owning analyze() context closes the actual workbook.
