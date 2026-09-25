module Processing
  # The /api/v1/processing/config payload (§5.3): a node's telescopes,
  # optical trains, every non-draft target with aliases and merged processing
  # settings, and equipment events. Its ETag lets Altair poll cheaply.
  class ConfigBuilder
    API_REVISION = 1
    EQUIPMENT_EVENT_WINDOW = 400.days

    def initialize(node)
      @node = node
    end

    def payload
      @payload ||= begin
        telescopes = @node.telescopes.includes(:optical_trains).order(:slug).to_a
        {
          api_revision: API_REVISION,
          node: { name: @node.name },
          telescopes: telescopes.map { |t| telescope_json(t) },
          targets: targets(telescopes).map { |t| target_json(t) },
          equipment_events: equipment_events(telescopes).map { |e| event_json(e) }
        }
      end
    end

    def etag
      Digest::SHA256.hexdigest(payload.to_json)[0, 32]
    end

    private

    def telescope_json(telescope)
      {
        slug: telescope.slug, timezone: telescope.timezone,
        latitude: telescope.latitude.to_f, longitude: telescope.longitude.to_f, elevation_m: telescope.elevation_m&.to_f,
        # Trains without optics can't be checked against an Altair rig, so they
        # aren't sent (the admin telescope and node pages flag them).
        optical_trains: telescope.optical_trains.select { |t| t.active? && t.complete_optics? }.sort_by(&:key).map { |train| train_json(train) }
      }
    end

    def train_json(train)
      {
        key: train.key, camera_type: train.camera_type, bayer_pattern: train.bayer_pattern,
        focal_length_mm: train.focal_length_mm.to_f, pixel_size_um: train.pixel_size_um.to_f,
        sensor_width_px: train.sensor_width_px, sensor_height_px: train.sensor_height_px,
        has_rotator: train.has_rotator,
        filters: train.filter_list.map { |f| { name: f["name"], aliases: Array(f["aliases"]), bandpass_nm: f["bandpass_nm"] }.compact },
        header_aliases: { telescope: Array(train.header_aliases["telescope"]), camera: Array(train.header_aliases["camera"]) }
      }
    end

    # Completed and cancelled targets stay: late frames and reruns still resolve.
    def targets(telescopes)
      Target.not_draft.where(telescope_id: telescopes.map(&:id))
            .includes(:project, :optical_train, astro_object: :aliases).order(:id)
    end

    def target_json(target)
      aliases = [ target.name, *target.astro_object&.alias_names ].compact.uniq
      {
        id: target.id, project_id: target.project_id, telescope: target.telescope.slug,
        optical_train: target.optical_train&.key, name: target.name, nina_name: target.nina_name, status: target.status,
        ra_deg: target.ra_deg.to_f, dec_deg: target.dec_deg.to_f, rotation_deg: target.rotation_deg&.to_f,
        aliases: aliases, processing_settings: target.effective_processing_settings
      }
    end

    def equipment_events(telescopes)
      EquipmentEvent.joins(:optical_train).where(optical_trains: { telescope_id: telescopes.map(&:id) })
                    .where(at: EQUIPMENT_EVENT_WINDOW.ago..).includes(:optical_train).order(:at, :id)
    end

    def event_json(event)
      { id: event.id, optical_train: event.optical_train.key, at: event.at.utc.iso8601, kind: event.kind,
        filter: event.filter, note: event.note }
    end
  end
end
