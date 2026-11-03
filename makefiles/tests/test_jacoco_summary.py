import csv
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


module_path = Path(__file__).resolve().parents[1] / 'jacoco_summary.py'
spec = importlib.util.spec_from_file_location('jacoco_summary', module_path)
jacoco = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jacoco)

HEADER = ['GROUP', 'PACKAGE', 'CLASS', 'INSTRUCTION_MISSED', 'INSTRUCTION_COVERED',
          'BRANCH_MISSED', 'BRANCH_COVERED', 'LINE_MISSED', 'LINE_COVERED',
          'COMPLEXITY_MISSED', 'COMPLEXITY_COVERED', 'METHOD_MISSED', 'METHOD_COVERED']


def fixture(**overrides):
    row = dict(zip(HEADER, ['module', 'example,package', 'Example', 900, 100,
                           8, 2, 3, 7, 19, 1, 1, 4]))
    row.update(overrides)
    return row


def csv_text(rows, header=HEADER):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


class CoverageSummaryTest(unittest.TestCase):
    def test_named_counters_are_not_instruction_or_complexity_columns(self):
        totals = jacoco.read_counters(io.StringIO(csv_text([fixture()])))
        self.assertEqual(jacoco.format_summary(totals).splitlines(), [
            'Lines:    70.0% (7/10)',
            'Branches: 20.0% (2/10)',
            'Methods:  80.0% (4/5)',
        ])

    def test_reordered_columns_and_quoted_package_names(self):
        totals = jacoco.read_counters(io.StringIO(csv_text([fixture()], list(reversed(HEADER)))))
        self.assertEqual(totals['BRANCH_COVERED'], 2)
        self.assertEqual(totals['LINE_COVERED'], 7)

    def test_multiple_modules_use_weighted_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'module-{index}.csv' for index in range(2)]
            paths[0].write_text(csv_text([fixture()]), encoding='utf-8')
            paths[1].write_text(csv_text([fixture(BRANCH_MISSED=0, BRANCH_COVERED=90)]), encoding='utf-8')
            self.assertIn('Branches: 92.0% (92/100)', jacoco.format_summary(jacoco.summarize(paths)))

    def test_zero_measurement_and_invalid_reports_are_explicit(self):
        empty = jacoco.read_counters(io.StringIO(csv_text([])))
        self.assertIn('not measured (0/0)', jacoco.format_summary(empty))
        with self.assertRaisesRegex(ValueError, 'Missing JaCoCo columns'):
            jacoco.read_counters(io.StringIO('LINE_COVERED\n1\n'))
        with self.assertRaisesRegex(ValueError, 'Negative JaCoCo counter'):
            jacoco.read_counters(io.StringIO(csv_text([fixture(BRANCH_COVERED=-1)])))


if __name__ == '__main__':
    unittest.main()
