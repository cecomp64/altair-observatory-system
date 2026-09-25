# Links frames to the catalogue objects in their field of view (§7.2). The
# footprint is computed once per pointing group (target, optical train,
# rounded pointing and rotation), not once per frame.
class FrameFovMatchJob < ApplicationJob
  queue_as :low

  POINTING_ROUND = 2 # decimals of a degree
  ROTATION_ROUND = 0

  def perform(frame_ids)
    frames = Frame.where(id: frame_ids).where.not(ra_deg: nil).where.not(fov_width_deg: nil).includes(:target)
    frames.group_by { |f| group_key(f) }.each_value do |group|
      reference = group.first
      matches = Astro::FovMatcher.new(
        ra: reference.ra_deg, dec: reference.dec_deg, width: reference.fov_width_deg,
        height: reference.fov_height_deg, rotation: reference.rotation_deg || 0
      ).matches
      group.each { |frame| store(frame, matches) }
    end
  end

  private

  def group_key(frame)
    [ frame.target_id, frame.optical_train_id, frame.ra_deg.to_f.round(POINTING_ROUND), frame.dec_deg.to_f.round(POINTING_ROUND),
      frame.rotation_deg.to_f.round(ROTATION_ROUND), frame.fov_width_deg.to_f.round(3), frame.fov_height_deg.to_f.round(3) ]
  end

  def store(frame, matches)
    primary_id = frame.target&.astro_object_id
    now = Time.current
    rows = matches.map do |m|
      { frame_id: frame.id, astro_object_id: m.object.id, angular_distance_arcmin: m.distance_arcmin,
        association_type: m.object.id == primary_id ? "primary" : "in_fov", created_at: now, updated_at: now }
    end
    Frame.transaction do
      frame.frame_objects.delete_all
      FrameObject.insert_all(rows) if rows.any?
      frame.update_column(:fov_matched_at, now)
    end
  end
end
