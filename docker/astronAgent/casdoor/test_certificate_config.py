"""Keep Casdoor startup configuration free of reusable signing private keys."""

import json
import pathlib
import unittest


class CertificateConfigurationTests(unittest.TestCase):
    def test_templates_do_not_import_historical_authentication_data(self):
        root = pathlib.Path(__file__).resolve().parent
        for name in ("init_data.json", "init_data.json.template"):
            with self.subTest(name=name):
                data = json.loads((root / "conf" / name).read_text(encoding="utf-8"))
                for field in ("tokens", "sessions", "records"):
                    self.assertEqual(data.get(field), [], field)
                for application in data["applications"]:
                    self.assertFalse(application.get("clientSecret"), application["name"])

    def test_templates_use_the_upstream_generated_builtin_certificate(self):
        root = pathlib.Path(__file__).resolve().parent
        for name in ("init_data.json", "init_data.json.template"):
            with self.subTest(name=name):
                source = (root / "conf" / name).read_text(encoding="utf-8")
                data = json.loads(source)
                self.assertNotIn("PRIVATE KEY", source)
                self.assertEqual(data.get("certs"), [])
                # Casdoor InitDb creates this before InitFromFile; there is no
                # need to distribute a sample private key for application links.
                for application in data["applications"]:
                    if application.get("cert"):
                        self.assertEqual(application["cert"], "cert-built-in")
                config = (root / "conf" / "app.conf").read_text(encoding="utf-8")
                self.assertIn("initDataNewOnly = true", config)


if __name__ == "__main__":
    unittest.main()
