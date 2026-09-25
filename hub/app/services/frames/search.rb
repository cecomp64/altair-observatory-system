require "csv"

module Frames
  # File search (§7.3 /frames): every filter is a GET param, so every view
  # is linkable.
  class Search
    FILTERS = %i[q ra dec radius project_id target_id telescope optical_train filter image_type night_from night_to
                 exposure gain binning status storage unassigned].freeze
    GROUPS = %w[night target telescope].freeze

    attr_reader :params

    def initialize(scope, params)
      @scope = scope
      @params = params.to_h.symbolize_keys.slice(*FILTERS, :group)
    end

    def relation
      @relation ||= begin
        rel = @scope
        rel = by_name(rel, params[:q]) if params[:q].present?
        if params[:ra].present? && params[:dec].present?
          ra = CoordinateParser.parse_ra(params[:ra])
          dec = CoordinateParser.parse_dec(params[:dec])
          rel = rel.cone(ra, dec, (params[:radius].presence || 1).to_f) if ra && dec
        end
        rel = rel.where(project_id: params[:project_id]) if params[:project_id].present?
        rel = rel.where(target_id: params[:target_id]) if params[:target_id].present?
        rel = rel.where(telescope_id: Telescope.where(slug: params[:telescope]).select(:id)) if params[:telescope].present?
        rel = rel.where(optical_train_id: OpticalTrain.where(key: params[:optical_train]).select(:id)) if params[:optical_train].present?
        rel = rel.where(filter: params[:filter]) if params[:filter].present?
        rel = rel.where(image_type: params[:image_type]) if params[:image_type].present?
        rel = rel.where(night: Date.parse(params[:night_from])..) if valid_date?(params[:night_from])
        rel = rel.where(night: ..Date.parse(params[:night_to])) if valid_date?(params[:night_to])
        rel = rel.where(exposure_s: params[:exposure].to_f - 0.5..params[:exposure].to_f + 0.5) if params[:exposure].present?
        rel = rel.where(gain: params[:gain]) if params[:gain].present?
        rel = rel.where(binning: params[:binning]) if params[:binning].present?
        rel = rel.where(status: params[:status]) if params[:status].present?
        rel = storage(rel, params[:storage]) if params[:storage].present?
        rel = rel.unassigned if params[:unassigned] == "1"
        rel
      end
    end

    def group
      params[:group].presence_in(GROUPS)
    end

    # Total exposure hours by filter (lights) and frames per month.
    def stats
      lights = relation.lights
      {
        hours_by_filter: lights.group(:filter).sum(:exposure_s).transform_values { |s| (s.to_f / 3600).round(2) }.sort_by { |f, _| f.to_s }.to_h,
        frames_by_month: relation.group(Arel.sql("to_char(night, 'YYYY-MM')")).order(Arel.sql("1")).count,
        count: relation.count
      }
    end

    def groups
      return nil unless group

      column = { "night" => :night, "target" => :target_id, "telescope" => :telescope_id }.fetch(group)
      rows = relation.group(column).order(column => :desc).pluck(column, Arel.sql("count(*)"), Arel.sql("coalesce(sum(exposure_s) filter (where image_type = 'light'), 0)"))
      labels = case group
      when "target" then Target.where(id: rows.map(&:first)).pluck(:id, :name).to_h
      when "telescope" then Telescope.where(id: rows.map(&:first)).pluck(:id, :name).to_h
      else {}
      end
      rows.map { |key, count, seconds| { key: key, label: labels.fetch(key, key.nil? ? "Unassigned" : key.to_s), count: count, hours: (seconds.to_f / 3600).round(2) } }
    end

    def to_csv(frames)
      CSV.generate do |csv|
        csv << %w[sha256 file_name night date_obs image_type filter exposure_s telescope optical_train target nas_path s3_key s3_class status]
        frames.includes(:telescope, :optical_train, :target).find_each do |f|
          csv << [ f.sha256, f.file_name, f.night, f.date_obs&.utc&.iso8601, f.image_type, f.filter, f.exposure_s&.to_f,
                   f.telescope.slug, f.optical_train.key, f.target&.nina_name, (f.logical_path if f.storage["nas"]),
                   (f.logical_path if f.storage["s3"]), f.storage["s3"], f.status ]
        end
      end
    end

    private

    # Object name or alias: the target's name, the object header, or a
    # catalogue object in the field of view.
    def by_name(rel, q)
      like = "%#{ActiveRecord::Base.sanitize_sql_like(q)}%"
      object_ids = AstroObject.search(q).limit(50).pluck(:id)
      rel.where(target_id: Target.where("targets.name ILIKE ?", like).or(Target.where(astro_object_id: object_ids)).select(:id))
         .or(rel.where("frames.object_header ILIKE ?", like))
         .or(rel.where(id: FrameObject.where(astro_object_id: object_ids).select(:frame_id)))
    end

    def storage(rel, tier)
      case tier
      when "nas" then rel.where("(frames.storage->>'nas')::boolean")
      when "s3_only" then rel.where("NOT coalesce((frames.storage->>'nas')::boolean, false) AND frames.storage->>'s3' IS NOT NULL")
      else rel.where("frames.storage->>'s3' = ?", tier)
      end
    end

    def valid_date?(value)
      value.present? && Date.parse(value.to_s)
    rescue Date::Error
      false
    end
  end
end
