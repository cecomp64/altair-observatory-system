module Frames
  # POST /processing/frames:batch (§5.3): upserts frames by sha256, each item
  # succeeding or failing on its own. A frame is never rejected because its
  # target is unknown (it is stored unlinked), and a manual assignment made in
  # the Hub always wins over Altair's.
  class BatchUpserter
    REQUIRED = %w[sha256 altair_frame_id origin telescope optical_train image_type night date_obs file_name logical_path status].freeze
    SHA256 = /\A[0-9a-f]{64}\z/

    attr_reader :affected_target_ids, :fov_frame_ids

    def initialize(node)
      @node = node
      @telescopes = node.telescopes.includes(:optical_trains).index_by(&:slug)
      @affected_target_ids = Set.new
      @fov_frame_ids = []
      @collected = Hash.new(0) # [target_id, night, filter] => new lights
    end

    def upsert(items)
      items = Array(items)
      existing = Frame.where(sha256: items.filter_map { |i| i["sha256"] if i.is_a?(Hash) }).index_by(&:sha256)
      targets = Target.where(id: items.filter_map { |i| i["target_id"] if i.is_a?(Hash) }).includes(:exposure_plans, :project).index_by(&:id)

      results = items.map do |item|
        upsert_one(item, existing, targets)
      rescue ActiveRecord::RecordInvalid, ArgumentError => e
        { sha256: item.is_a?(Hash) ? item["sha256"].to_s : "", status: "error", error: e.message }
      end
      emit_collected_events
      results
    end

    private

    def upsert_one(item, existing, targets)
      raise ArgumentError, "frame must be an object" unless item.is_a?(Hash)

      missing = REQUIRED.select { |k| item[k].blank? }
      raise ArgumentError, "missing #{missing.join(', ')}" if missing.any?
      raise ArgumentError, "sha256 must be 64 lower-case hex characters" unless item["sha256"].match?(SHA256)

      telescope = @telescopes[item["telescope"]] or raise ArgumentError, "telescope #{item['telescope'].inspect} is not served by this node"
      train = telescope.optical_trains.find { |t| t.key == item["optical_train"] } or
        raise ArgumentError, "optical train #{item['optical_train'].inspect} not found on #{telescope.slug}"

      frame = existing[item["sha256"]] || Frame.new(sha256: item["sha256"])
      was_new = frame.new_record?
      previous_target_id = frame.target_id
      previous_pointing = [ frame.ra_deg, frame.dec_deg ]

      raw_filter = item["filter"].presence
      canonical = raw_filter && train.canonical_filter(raw_filter)
      frame.assign_attributes(
        processing_node: @node, altair_frame_id: item["altair_frame_id"], origin: item["origin"],
        telescope: telescope, optical_train: train, image_type: item["image_type"], night: item["night"],
        date_obs: item["date_obs"], object_header: item["object_header"],
        filter: canonical || raw_filter, filter_known: raw_filter.nil? || canonical.present? || train.filter_names.empty?,
        exposure_s: item["exposure_s"], gain: item["gain"], offset: item["offset"], binning: item["binning"],
        readout_mode: item["readout_mode"], sensor_temp_c: item["sensor_temp_c"], rotator_pos: item["rotator_pos"],
        rotator_units: item["rotator_units"], ra_deg: item["ra_deg"], dec_deg: item["dec_deg"],
        rotation_deg: item["rotation_deg"], width_px: item["width_px"], height_px: item["height_px"],
        file_name: item["file_name"], logical_path: item["logical_path"], status: item["status"],
        status_reason: item["status_reason"], quality: item["quality"] || frame.quality || {},
        storage: item["storage"] || frame.storage || {}, headers: item["headers"] || frame.headers || {}
      )
      assign_fov(frame, train)
      assign_target(frame, item, targets, telescope, train) unless frame.manual?
      frame.exposure_plan = PlanMatcher.match(frame)
      frame.save!

      @affected_target_ids << frame.target_id if frame.target_id
      @affected_target_ids << previous_target_id if previous_target_id && previous_target_id != frame.target_id
      @fov_frame_ids << frame.id if frame.ra_deg && (was_new || previous_pointing != [ frame.ra_deg, frame.dec_deg ])
      @collected[[ frame.target_id, frame.night, frame.filter ]] += 1 if was_new && frame.light? && frame.target_id

      { sha256: frame.sha256, id: frame.id, target_id: frame.target_id, exposure_plan_id: frame.exposure_plan_id, status: "ok" }
    end

    # Altair's link, when the target exists on this frame's telescope (and
    # train, when the target has one); otherwise the frame is unlinked.
    def assign_target(frame, item, targets, telescope, train)
      target = targets[item["target_id"].to_i] if item["target_id"]
      target = nil if target && (target.telescope_id != telescope.id || (target.optical_train_id && target.optical_train_id != train.id))
      frame.target = target
      frame.project = target&.project
      frame.assignment_source = target ? (item["assignment_source"].presence_in(%w[header_token name coords]) || "header_token") : "unlinked"
    end

    def assign_fov(frame, train)
      scale = train.pixel_scale_arcsec
      return unless scale && frame.width_px && frame.height_px

      frame.fov_width_deg = (frame.width_px * scale / 3600.0).round(5)
      frame.fov_height_deg = (frame.height_px * scale / 3600.0).round(5)
    end

    # One frames_collected event per (target, night, filter) per hour (§5.3).
    def emit_collected_events
      @collected.each do |(target_id, night, filter), count|
        target = Target.find(target_id)
        recent = target.target_events.frames_collected.where(created_at: 1.hour.ago..)
                       .where("payload->>'night' = ? AND payload->>'filter' = ?", night.to_s, filter.to_s)
        if (event = recent.first)
          event.update_columns(payload: event.payload.merge("count" => event.payload["count"].to_i + count), updated_at: Time.current)
        else
          target.target_events.create!(event_type: :frames_collected, payload: { night: night.to_s, filter: filter, count: count })
        end
      end
    end
  end
end
