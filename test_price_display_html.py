import unittest
from html.parser import HTMLParser

from price_display import price_label_html, price_label_button


class StrikeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_strike = False
        self.struck = ''
        self.visible = ''

    def handle_starttag(self, tag, attrs):
        if tag == 's':
            self.in_strike = True

    def handle_endtag(self, tag):
        if tag == 's':
            self.in_strike = False

    def handle_data(self, data):
        self.visible += data
        if self.in_strike:
            self.struck += data


class PriceDisplayHtmlTests(unittest.TestCase):
    def test_discount_strikes_only_original_price(self):
        product = {'price': 115000, 'compare_price': 126000}
        parser = StrikeParser()
        parser.feed(price_label_html(product, suffix=''))
        self.assertEqual(parser.struck, '126,000')
        self.assertIn('115,000', parser.visible)
        self.assertNotIn('<s>', price_label_button(product))

    def test_no_discount_and_additional_user_price(self):
        self.assertNotIn('<s>', price_label_html({'price': 115000, 'compare_price': 0}))
        parser = StrikeParser()
        parser.feed(price_label_html({'price': 115000, 'compare_price': 126000}, 125000))
        self.assertEqual(parser.struck, '136,000')
        self.assertIn('125,000', parser.visible)
