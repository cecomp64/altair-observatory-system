module ProcessingHelpers
  def sha(n) = Digest::SHA256.hexdigest("frame-#{n}")

  def frame_payload(n, overrides = {})
    {
      "sha256" => sha(n), "altair_frame_id" => 1000 + n, "origin" => "collect",
      "telescope" => telescope.slug, "optical_train" => train.key, "target_id" => target.id,
      "assignment_source" => "header_token", "image_type" => "light", "night" => "2026-09-24",
      "date_obs" => (Time.utc(2026, 9, 25, 6) + n.minutes).iso8601, "object_header" => target.nina_name,
      "filter" => "Ha", "exposure_s" => 300, "gain" => 100, "offset" => 50, "binning" => "1x1",
      "ra_deg" => target.ra_deg.to_f, "dec_deg" => target.dec_deg.to_f, "width_px" => 6248, "height_px" => 4176,
      "file_name" => "2026-09-24_Ha_300.00s_#{format('%04d', n)}.fits",
      "logical_path" => "raw/#{train.key}/2026-09-24/#{target.nina_name}/LIGHT/Ha/#{n}.fits",
      "status" => "valid", "storage" => { "nas" => true, "s3" => nil }, "headers" => { "IMAGETYP" => "LIGHT" }
    }.merge(overrides)
  end

  def node_headers(key = node_key)
    { "Authorization" => "Bearer #{key.plaintext_token}", "Content-Type" => "application/json" }
  end
end

RSpec.configure { |c| c.include ProcessingHelpers }
