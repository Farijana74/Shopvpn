import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class LegacyMigrationShellTests(unittest.TestCase):
    def run_flow(self,confirmation):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);project=base/'project';project.mkdir();(project/'venv'/'bin').mkdir(parents=True)
            source=base/'old.db';source.touch();log=base/'commands.log';bin_dir=base/'bin';bin_dir.mkdir()
            systemctl=bin_dir/'systemctl'
            systemctl.write_text('''#!/bin/bash
printf 'systemctl %s\\n' "$*" >> "$MIGRATION_TEST_LOG"
if [ "$1" = is-active ]; then
 case "$3" in oldbot|oldbot-api|newbot|newbot-adminpanel) exit 0;; *) exit 1;; esac
fi
exit 0
''');systemctl.chmod(0o755)
            python=project/'venv'/'bin'/'python'
            python.write_text('''#!/bin/bash
printf 'python %s\\n' "$*" >> "$MIGRATION_TEST_LOG"
printf '{"ready":true}\\n'
exit 0
''');python.chmod(0o755)
            env={**os.environ,'PATH':str(bin_dir)+':'+os.environ['PATH'],'SHOPVPN_INSTALL_DIR':str(project),'SHOPVPN_LEGACY_DB':str(source),'SHOPVPN_LEGACY_SERVICE':'oldbot','SHOPVPN_SERVICE_NAME':'newbot','MIGRATION_TEST_LOG':str(log)}
            script=Path(__file__).with_name('legacy_migration.sh')
            result=subprocess.run(['bash',str(script)],input='\n1\n\ny\n'+confirmation+'\n',text=True,capture_output=True,env=env,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            return log.read_text()

    def test_cancel_resumes_all_previously_running_services_without_applying(self):
        log=self.run_flow('n')
        self.assertIn('systemctl start oldbot\n',log)
        self.assertIn('systemctl start oldbot-api\n',log)
        self.assertIn('systemctl start newbot\n',log)
        self.assertNotIn(' apply ',log)

    def test_simple_yes_does_not_authorize_replacement(self):
        log=self.run_flow('y')
        self.assertNotIn(' apply ',log)
        self.assertIn('systemctl start newbot\n',log)

    def test_success_keeps_old_services_stopped_and_resumes_new_services(self):
        log=self.run_flow('REPLACE')
        self.assertIn(' apply ',log)
        self.assertIn('--mode replace',log)
        self.assertIn('systemctl stop oldbot-api\n',log)
        self.assertNotIn('systemctl start oldbot\n',log)
        self.assertNotIn('systemctl start oldbot-api\n',log)
        self.assertIn('systemctl start newbot\n',log)
        self.assertIn('systemctl start newbot-adminpanel\n',log)
