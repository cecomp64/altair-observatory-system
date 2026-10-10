module DynamicCataloguesHelper
  # AAVSO's other_info holds text with links written [[url description]]
  # (or [[description url]]). Everything else is escaped.
  def aavso_info(text)
    parts = text.to_s.split(/(\[\[.+?\]\])/).map do |part|
      inner = part[/\A\[\[(.+)\]\]\z/, 1]
      next part unless inner

      words = inner.split
      url = words.find { |w| w.match?(%r{\Ahttps?://}) }
      next inner unless url

      link_to((words - [ url ]).join(" ").presence || url, url, target: "_blank", rel: "noopener", class: "text-indigo-600 hover:underline")
    end
    safe_join(parts)
  end

  def aavso_mag_range(details)
    bright = details["max_mag"] && "#{details['max_mag']}#{details['max_mag_band']}"
    faint = details["min_mag"] && "#{details['min_mag']}#{details['min_mag_band']}"
    [ bright, faint ].compact.join(" – ").presence
  end

  def aavso_vsx_url(name)
    "https://vsx.aavso.org/index.php?#{{ view: 'results.get', ident: name }.to_query}"
  end

  def aavso_campaign_url(id)
    format(Catalogue::Dynamic::AavsoCampaigns::CAMPAIGN_URL, id.to_i)
  end

  # "2025-02-04 – 2027-12-31" as "Feb 4, 2025 – Dec 31, 2027", or "since … (ongoing)".
  def aavso_campaign_window(campaign)
    start, finish = campaign.values_at("start_date", "end_date").map { |d| d.present? ? (Date.iso8601(d) rescue nil) : nil }
    return nil unless start || finish
    return "since #{l(start, format: :long)}, ongoing" if finish.nil?

    "#{start ? l(start, format: :long) : '?'} – #{l(finish, format: :long)}"
  end

  def comet_cobs_url(id)
    format(Catalogue::Dynamic::BrightComets::COMET_URL, id.to_i)
  end

  # COBS dates are "2026-08-02 02:29" or "2026-08-03".
  def comet_date(value)
    Date.iso8601(value.to_s[0, 10]).strftime("%B %-d, %Y")
  rescue Date::Error
    value.to_s
  end
end
