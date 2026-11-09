import unittest
from avdlite.sources import select_image


def entry(api, tag='google_apis', revision=1):
    return f'''<remotePackage path="system-images;android-{api};{tag};x86_64">
      <type-details><api-level>{api}</api-level><abi>x86_64</abi></type-details>
      <revision><major>{revision}</major></revision>
      <archives><archive><complete><size>123</size><checksum type="sha1">{'a' * 40}</checksum><url>image.zip</url></complete></archive></archives>
    </remotePackage>'''


class SourceSelectionTests(unittest.TestCase):
    def test_latest_excludes_previews_and_old_android(self):
        xml = '<repository>' + ''.join(entry(v) for v in ('34', '35', '36.1', '37.2-beta3', 'canary-20261104')) + '</repository>'
        self.assertEqual(select_image(xml)['api'], '36.1')

    def test_explicit_page_size_and_version(self):
        xml = '<repository>' + entry('35') + entry('35', 'google_apis_ps16k') + entry('37.2', 'google_apis_ps16k') + '</repository>'
        self.assertEqual(select_image(xml, '35', '4k')['tag'], 'google_apis')
        self.assertEqual(select_image(xml, '35', 'auto')['tag'], 'google_apis_ps16k')
        with self.assertRaises(ValueError):
            select_image(xml, '37.2', '4k')

    def test_rejects_unsupported_or_injected_inputs(self):
        for value in ('34', '15', 'latest; echo broken', '../../35', '37.2-beta3'):
            with self.assertRaises(ValueError):
                select_image('<repository/>', value)


if __name__ == '__main__':
    unittest.main()
