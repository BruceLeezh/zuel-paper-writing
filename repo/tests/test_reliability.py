"""可靠性回归：断网、坏响应、解析丢失和数据口径。均使用本地构造数据。"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill
from zuel import tables, verify, feeds, net
from zuel.model import Item

class ReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
    def file(self, text, suffix='.txt'):
        p = Path(self.tmp.name)/('input'+suffix)
        p.write_text(text, encoding='utf-8')
        return str(p)
    def item(self, doi='10.1111/a'):
        return Item(src='test', field='literature', title='A valid title', ident=doi)
    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', ['skill.py']+args), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = skill.main()
        return code, out.getvalue(), err.getvalue()
    def test_invalid_json_is_failure(self):
        with mock.patch.object(net, 'fetch', return_value='<html>error</html>'):
            with self.assertRaises(net.NetError): verify.lookup('10.1111/a')
    def test_wrong_payload_is_failure(self):
        for payload in ('[]', '{}', '{"message": {}}', '{"message":{"title":"wrong"}}'):
            with self.subTest(payload=payload), mock.patch.object(net, 'fetch', return_value=payload):
                with self.assertRaises(net.NetError): verify.lookup('10.1111/a')
    def test_empty_title_is_failure(self):
        with mock.patch.object(net, 'fetch', return_value='{"message":{"title":[""]}}'):
            with self.assertRaises(net.NetError): verify.lookup('10.1111/a')
    def test_non404_url_is_not_missing(self):
        with mock.patch.object(net, 'fetch', side_effect=net.NetError('HTTP 503: https://x/404 Not Found')):
            with self.assertRaises(net.NetError): verify.lookup('10.1111/a')
    def test_limit_does_not_lose_later_counts(self):
        items=[self.item(),self.item('10.1111/b'),self.item(''),self.item('ISBN 123')]
        with mock.patch.object(verify, 'lookup', return_value={'title':['A valid title']}):
            res=verify.verify_items(items,limit=1)
        self.assertEqual((res['checked'],res['truncated'],res['skipped'],res['not_doi']),(1,1,1,1))
    def test_limit_must_be_positive(self):
        for limit in (0,-1,True):
            with self.assertRaises(ValueError): verify.verify_items([],limit=limit)
    def test_bad_record_does_not_abort_batch(self):
        with mock.patch.object(verify,'lookup',side_effect=[{'title':['A valid title'],'author':[None]},{'title':['A valid title']}]):
            res=verify.verify_items([self.item(),self.item('10.1111/b')])
        self.assertEqual((len(res['failed']),res['ok']),(1,1))
    def test_duplicate_author_does_not_pass(self):
        item=self.item();item.people=['Smith','Smith']
        issues=verify.compare(item,{'title':['A valid title'],'author':[{'family':'Smith'},{'family':'Jones'}]})
        self.assertTrue(any(i['kind']=='field_diff' for i in issues))
    def test_surname_substring_not_a_match(self):
        item=self.item();item.people=['Smith']
        self.assertTrue(verify.compare(item,{'title':['A valid title'],'author':[{'family':'Li'}]}))
    def test_network_failure_exit_is_two(self):
        path=self.file('%0 Journal Article\n%T A valid title\n%R 10.1111/a\n')
        with mock.patch.object(verify,'lookup',side_effect=net.NetError('offline')):
            code,out,err=self.call(['verify',path,'--json'])
        self.assertEqual(code,2);self.assertEqual(len(json.loads(out)['failed']),1)
    def test_offline_report_never_claims_success(self):
        path=self.file('%T A valid title\n%R 10.1111/a\n')
        with mock.patch.object(verify,'lookup',side_effect=net.NetError('offline')):
            code,out,err=self.call(['verify',path])
        self.assertNotIn('未发现字段冲突',out);self.assertEqual(code,2)
    def test_truncated_exit_is_two(self):
        path=self.file('%0 Journal Article\n%T A valid title\n%R 10.1111/a\n%0 Journal Article\n%T A valid title\n%R 10.1111/b')
        with mock.patch.object(verify,'lookup',return_value={'title':['A valid title']}):
            code,out,err=self.call(['verify',path,'--limit','1','--json'])
        self.assertEqual(code,2);self.assertEqual(json.loads(out)['truncated'],1)
    def test_endnote_no_blank_separator(self):
        items,_=tables.read_export(self.file('%0 Journal Article\n%T First\n%0 Book\n%T Second'))
        self.assertEqual([i.title for i in items],['First','Second'])
    def test_refworks_no_blank_separator(self):
        items,_=tables.read_export(self.file('RT Journal Article\nT1 First\nRT Book\nT1 Second'))
        self.assertEqual([i.title for i in items],['First','Second'])
    def test_missing_title_is_not_dropped(self):
        items,_=tables.read_export(self.file('%0 Journal Article\n%A Someone\n%D 2020'))
        self.assertEqual(len(items),1);self.assertEqual(items[0].title,'')
        self.assertEqual(verify.completeness(items)[0]['kind'],'no_title')
    def test_refworks_missing_title_is_not_dropped(self):
        items,_=tables.read_export(self.file('RT Book\nA1 Someone\nYR 2020'))
        self.assertEqual(len(items),1);self.assertEqual(items[0].title,'')
    def test_unparsed_counts_lines(self):
        path=self.file('[1] 张三. 研究[J]. 某刊, 2020.\n坏条目一\n坏条目二')
        code,out,err=self.call(['verify',path,'--json'])
        self.assertEqual(json.loads(out)['unparsed_lines'],2);self.assertEqual(code,2)
    def test_empty_inputs_rejected(self):
        code,out,err=self.call(['check',self.file('  '),'--json'])
        self.assertEqual(code,2);self.assertEqual(out,'');self.assertIn('空文档',err)
        code,out,err=self.call(['cites',self.file('')])
        self.assertEqual(code,2);self.assertIn('空',err)
        path=self.file('Stkcd,Accper,Typrep\n','.csv')
        code,out,err=self.call(['panel',path])
        self.assertEqual(code,2);self.assertIn('没有数据行',err)
    def test_invalid_docx_exit_two(self):
        code,out,err=self.call(['check',self.file('fake','.docx'),'--json'])
        self.assertEqual(code,2);self.assertEqual(out,'');self.assertNotIn('Traceback',err)
    def test_blank_query_rejected(self):
        with mock.patch.object(feeds,'fetch_items') as fetch:
            code,out,err=self.call(['find','--q',' '])
        self.assertEqual(code,2);fetch.assert_not_called()
    def test_duplicate_header_rejected(self):
        with self.assertRaises(ValueError): tables.read_panel(self.file('Stkcd,Stkcd,A\n1,1,2','.csv'))
    def test_extra_cell_rejected(self):
        with self.assertRaises(ValueError): tables.read_panel(self.file('Stkcd,Accper,A\n1,2020,2,3','.csv'))
    def test_short_row_counts_missing(self):
        prof=tables.profile(['Stkcd','Accper','A'],[['1','2020']])
        self.assertEqual(prof['missing']['A'],100)
    def test_missing_tokens_counted(self):
        prof=tables.profile(['A'],[['NA'],['null'],['--'],['0']])
        self.assertEqual(prof['missing']['A'],75)
    def test_multitoken_surname_matches_fullname(self):
        rec = {'title': ['A valid title'],
               'author': [{'family': 'Van der Berg', 'given': 'Anne'}],
               'issued': {'date-parts': [[2020]]}}
        it = Item(src='t', field='literature', title='A valid title',
                  people=['Anne Van der Berg'], ident='10.1/x')
        self.assertEqual(verify.compare(it, rec), [])

    def test_large_text_file_rejected(self):
        import itertools
        path = Path(self.tmp.name) / 'big.txt'
        chunk = ('字' * 100 + chr(10)) * 100000          # ~21 MB
        with path.open('w', encoding='utf-8') as f:
            for _ in range(6):                            # ~128 MB 字符
                f.write(chunk)
        with self.assertRaises(ValueError) as cm:
            tables.read_export(str(path))
        self.assertIn('上限', str(cm.exception))

    def test_desc_row_requires_known_field_names(self):
        self.assertTrue(tables._is_desc_row(
            ['证券代码', '统计截止日期'], ['Stkcd', 'Accper']))
        self.assertFalse(tables._is_desc_row(['北京', '制造业'], ['城市', '行业']))

    def test_throttle_before_retry(self):
        calls = []
        real_sleep = None
        with mock.patch.object(net, '_throttle', side_effect=lambda u, i: calls.append(i)):
            with mock.patch.object(net, 'time') as tmod:
                tmod.monotonic.return_value = 0.0
                tmod.sleep.side_effect = lambda s: calls.append(('sleep', s))
                with mock.patch.object(net.urllib.request, 'build_opener') as bo:
                    opener = mock.MagicMock()
                    resp = mock.MagicMock()
                    resp.read.side_effect = [b'x' * (net.MAX_BYTES + 1)]
                    opener.open.return_value.__enter__ = lambda s: resp
                    opener.open.return_value.__exit__ = lambda s, *a: False
                    bo.return_value = opener
                    from zuel import net as _n
                    try:
                        _n.fetch('https://x.example/q', interval=3.0)
                    except _n.NetError:
                        pass
        self.assertIn(3.0, calls)
    def test_chinese_data_not_removed(self):
        self.assertFalse(tables._is_desc_row(['北京','制造业'],['城市','行业']))
    def test_key_conflicts_counted(self):
        prof=tables.profile(['Stkcd','Accper','Typrep','A'],[['1','2020','A','10'],['1','2020','A','20'],['1','2020','B','30']])
        self.assertEqual(prof['key_duplicates'],1);self.assertEqual(prof['dup'],0)
    def test_report_selection_is_explicit(self):
        path=self.file('Stkcd,Accper,Typrep,A\n1,2020,A,10\n1,2020,B,20','.csv')
        code,out,err=self.call(['panel',path,'--report-type','A','--json'])
        self.assertEqual(code,0);prof=json.loads(out)['profile']
        self.assertEqual((prof['rows'],prof['selection']['input_rows']),(1,2))
        self.assertIn('B,20',Path(path).read_text(encoding='utf-8'))
    def test_report_selection_empty_rejected(self):
        path=self.file('Stkcd,Accper,Typrep\n1,2020,B','.csv')
        self.assertEqual(self.call(['panel',path,'--report-type','A'])[0],2)
    def test_quote_json_is_parseable(self):
        with mock.patch.object(feeds,'sina',return_value=[self.item()]):
            code,out,err=self.call(['quote','--code','600519','--json'])
        self.assertEqual(code,0);self.assertIsInstance(json.loads(out),list)
    def test_macro_json_is_parseable(self):
        with mock.patch.object(feeds,'worldbank',return_value=[self.item()]),mock.patch.object(feeds,'worldbank_meta',return_value={'name':'test'}):
            code,out,err=self.call(['macro','--indicator','test','--json'])
        self.assertEqual(code,0);self.assertIn('metadata',json.loads(out))
    def test_macro_all_null_is_error(self):
        with mock.patch.object(feeds,'fetch',return_value='[{},[{"value":null}]]'):
            with self.assertRaises(net.NetError): feeds.worldbank('test')
    def test_macro_blank_identifier_is_error(self):
        with self.assertRaises(ValueError): feeds.worldbank(' ')

    def test_unknown_endnote_line_is_preserved(self):
        items,_=tables.read_export(self.file('%T Title\nunknown data'))
        self.assertEqual([i.raw['unparsed'] for i in items if 'unparsed' in i.raw],[['unknown data']])
    def test_unknown_refworks_line_is_preserved(self):
        items,_=tables.read_export(self.file('T1 Title\nunknown data'))
        self.assertEqual([i.raw['unparsed'] for i in items if 'unparsed' in i.raw],[['unknown data']])

if __name__=='__main__': unittest.main()
