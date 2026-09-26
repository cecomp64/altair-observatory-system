require "kramdown"
require "kramdown-parser-gfm"

# Renders an Altair Markdown report (GFM: headings, paragraphs, tables, lists,
# emphasis, code) to sanitized HTML. Reports come from a processing node, so
# they are treated as untrusted: no raw HTML, scripts, images or styles
# survive, and links are limited to http(s).
class ReportRenderer
  TAGS = %w[h1 h2 h3 h4 p br hr ul ol li strong em code pre blockquote table thead tbody tr th td a].freeze
  ATTRIBUTES = %w[href title style].freeze

  def self.render(markdown)
    html = Kramdown::Document.new(markdown.to_s, input: "GFM", hard_wrap: false, auto_ids: false,
                                  html_to_native: false, parse_block_html: false, parse_span_html: false).to_html
    clean = Rails::HTML5::SafeListSanitizer.new.sanitize(html, tags: TAGS, attributes: ATTRIBUTES)
    fragment = Nokogiri::HTML5.fragment(clean)
    fragment.css("a").each do |a|
      href = a["href"].to_s
      href.match?(%r{\Ahttps?://}i) ? a.set_attribute("rel", "noopener nofollow") : a.remove_attribute("href")
    end
    # Only kramdown's table alignment survives as a style.
    fragment.css("[style]").each do |node|
      align = node["style"][/\Atext-align:\s*(left|right|center);?\z/, 1]
      align ? node.set_attribute("style", "text-align: #{align}") : node.remove_attribute("style")
    end
    fragment.to_html.html_safe # rubocop:disable Rails/OutputSafety -- sanitized above
  end
end
