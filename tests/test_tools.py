import unittest,tempfile,os,json,sys,shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import dev_agent as agent
class ToolTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'repo';self.root.mkdir();self.saved_root=agent.PROJECT_ROOT;agent.PROJECT_ROOT=str(self.root)
 def tearDown(self):agent.PROJECT_ROOT=self.saved_root;self.tmp.cleanup()
 def test_root_files_are_listed(self):
  (self.root/'README.md').write_text('demo');self.assertEqual(agent.list_project_files(),['README.md'])
 def test_sibling_prefix_escape_rejected(self):
  sibling=self.root.parent/'repo-other';sibling.mkdir();(sibling/'file.txt').write_text('outside')
  with self.assertRaises(ValueError):agent.read_file('../repo-other/file.txt')
 def test_symlink_escape_rejected(self):
  target=self.root.parent/'private.txt';target.write_text('outside');(self.root/'alias').symlink_to(target)
  with self.assertRaises(ValueError):agent.read_file('alias')
 def test_credentials_are_not_listed_or_read(self):
  for name in ['.env','.env.local','id_rsa','service.pem']:
   (self.root/name).write_text('test-only')
   with self.assertRaises(ValueError):agent.read_file(name)
  self.assertEqual(agent.list_project_files(),[])
 def test_line_range(self):
  (self.root/'demo.txt').write_text('a\nb\nc\n');self.assertEqual(agent.read_file('demo.txt',2,2),'b\n')
  with self.assertRaises(ValueError):agent.read_file('demo.txt',0)
 def test_declined_patch_does_not_write(self):
  p=self.root/'demo.txt';p.write_text('hello')
  with patch.object(agent,'_confirm_action',return_value=False):self.assertIn('declined',agent.apply_patch('demo.txt','hello','world'))
  self.assertEqual(p.read_text(),'hello')
 def test_replacement_does_not_repatch_new_text(self):
  p=self.root/'demo.txt';p.write_text('foo foo')
  with patch.object(agent,'_confirm_action',return_value=True):agent.apply_patch('demo.txt','foo','foobar',0)
  self.assertEqual(p.read_text(),'foobar foobar');backups=list((self.root/'.dev_agent/backups').iterdir());self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'foo foo')
 def test_empty_and_ambiguous_patches_rejected(self):
  (self.root/'demo.txt').write_text('foo foo')
  for old in ['', 'foo']:
   with self.assertRaises(ValueError):agent.apply_patch('demo.txt',old,'bar')
 def test_concurrent_edit_is_preserved(self):
  p=self.root/'demo.txt';p.write_text('before')
  def approve(_):p.write_text('concurrent');return True
  with patch.object(agent,'_confirm_action',side_effect=approve):
   with self.assertRaises(ValueError):agent.apply_patch('demo.txt','before','after')
  self.assertEqual(p.read_text(),'concurrent')
 def test_shell_requires_approval(self):
  with patch.object(agent,'_confirm_action',return_value=False),patch('subprocess.run') as run:
   self.assertIn('declined',agent.run_shell('anything'));run.assert_not_called()
 def test_approved_shell_returns_exit_code(self):
  cmd=shlex.quote(sys.executable)+' -c '+shlex.quote("print('demo')")
  with patch.object(agent,'_confirm_action',return_value=True):self.assertIn('Exit code: 0\ndemo',agent.run_shell(cmd))
 def test_image_payload_uses_url_string_and_mime(self):
  (self.root/'demo.jpg').write_bytes(b'test-image');captured={}
  def create(**kwargs):captured.update(kwargs);return SimpleNamespace(output_text='Demo image')
  fake=SimpleNamespace(responses=SimpleNamespace(create=create))
  with patch.object(agent,'client',fake),patch.object(agent,'_confirm_action',return_value=True):self.assertEqual(agent.analyze_image('demo.jpg'),'Demo image')
  image=captured['input'][0]['content'][1];self.assertIsInstance(image['image_url'],str);self.assertTrue(image['image_url'].startswith('data:image/jpeg;base64,'));self.assertEqual(captured['model'],'gpt-4.1-mini')
 def test_image_decline_sends_nothing(self):
  (self.root/'demo.png').write_bytes(b'test');fake=SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs:self.fail('provider called')))
  with patch.object(agent,'client',fake),patch.object(agent,'_confirm_action',return_value=False):self.assertIn('nothing sent',agent.analyze_image('demo.png'))
 def test_large_patch_is_rejected_before_approval(self):
  p=self.root/'demo.txt';p.write_text('a')
  with patch.object(agent,'_confirm_action') as approve:
   with self.assertRaises(ValueError):agent.apply_patch('demo.txt','a','b'*20000)
   approve.assert_not_called()
  self.assertEqual(p.read_text(),'a')
 def test_read_size_and_list_limit(self):
  (self.root/'large.txt').write_text('x'*131073)
  with self.assertRaises(ValueError):agent.read_file('large.txt')
  with self.assertRaises(ValueError):agent.list_project_files(max_results=0)
 def test_offline_cli_has_no_state_or_key_requirement(self):
  import subprocess
  env=os.environ.copy();env.pop('OPENAI_API_KEY',None)
  result=subprocess.run([sys.executable,str(Path(agent.__file__).resolve()),'--check'],cwd=self.root,env=env,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr);self.assertFalse((self.root/'.dev_agent').exists())
 def test_sdk_tools_and_session_persist_without_provider_calls(self):
  import asyncio
  from agents import SQLiteSession
  state=self.root/'.dev_agent'
  with patch.object(agent,'STORAGE_DIR',str(state)),patch.object(agent,'PROMPT_PATH',str(state/'prompt.txt')),patch.object(agent,'SESSION_PATH',str(state/'session.sqlite')),patch.object(agent,'OpenAI') as provider:
   agent.initialize_runtime();self.assertEqual(len(agent.agent.tools),5);provider.assert_not_called()
   asyncio.run(agent.session.add_items([{'role':'user','content':'local test'}]))
   second=SQLiteSession(agent.PROJECT_KEY,db_path=str(state/'session.sqlite'))
   self.assertEqual(asyncio.run(second.get_items())[0]['content'],'local test');second.close();agent.session.close()
if __name__=='__main__':unittest.main()

