# Coordinates in views: one format everywhere (see CoordinateFormatter), with
# the decimal degrees in a tooltip.
module CoordinatesHelper
  def format_ra(degrees)
    coordinate_tag(CoordinateFormatter.ra(degrees), degrees)
  end

  def format_dec(degrees)
    coordinate_tag(CoordinateFormatter.dec(degrees), degrees)
  end

  def format_radec(ra, dec)
    return "—" if ra.nil? || dec.nil?

    safe_join([ "RA ", format_ra(ra), " · Dec ", format_dec(dec) ])
  end

  def format_latitude(degrees)
    coordinate_tag(CoordinateFormatter.latitude(degrees), degrees)
  end

  def format_longitude(degrees)
    coordinate_tag(CoordinateFormatter.longitude(degrees), degrees)
  end

  def format_site(telescope)
    safe_join([ format_latitude(telescope.latitude), ", ", format_longitude(telescope.longitude) ])
  end

  def format_angle(degrees, digits: 0)
    CoordinateFormatter.angle(degrees, digits: digits) || "—"
  end

  # The telescope's local time, kept current in the browser.
  def telescope_clock(telescope, now: Time.current)
    local = now.in_time_zone(telescope.time_zone)
    tag.time(local.strftime("%H:%M %Z"), datetime: local.iso8601, class: "tabular-nums",
                                         title: "Local time at #{telescope.name} (#{telescope.timezone})",
                                         data: { controller: "local-clock", local_clock_zone_value: telescope.timezone })
  end

  private

  def coordinate_tag(text, degrees)
    return "—" if text.nil?

    tag.span(text, class: "tabular-nums whitespace-nowrap", title: CoordinateFormatter.decimal(degrees))
  end
end
