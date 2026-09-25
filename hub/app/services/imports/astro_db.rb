require "sqlite3"

module Imports
  # One-off import of an astrophotography-database file (§8.4):
  #
  # - objects + aliases merge into the catalogue: same normalised alias AND
  #   coordinates within 1', else a new custom object;
  # - projects keep status and priority; with a telescope, each project
  #   target becomes a draft Target there, with one exposure plan per goal
  #   filter (median exposure of that project's images in the filter, else
  #   300 s; count = ceil(goal / exposure)); without one, the project is
  #   kept in "planning" with its goals in the description;
  # - showcases become Active Storage attachments;
  # - saved locations, settings and images are not imported (images come in
  #   through `altair index` with real SHA-256 identities).
  class AstroDb
    MERGE_RADIUS_DEG = 1.0 / 60
    DEFAULT_EXPOSURE_S = 300
    PROJECT_STATUSES = %w[active completed paused archived].freeze

    Report = Struct.new(:objects_merged, :objects_created, :objects_skipped, :projects_created, :projects_skipped,
                        :targets_created, :plans_created, :showcases, :warnings, keyword_init: true) do
      def to_s
        "objects merged #{objects_merged}, created #{objects_created}, skipped #{objects_skipped}; " \
          "projects created #{projects_created}, skipped #{projects_skipped}; targets #{targets_created}, " \
          "plans #{plans_created}; showcases #{showcases}" + (warnings.any? ? "\nwarnings:\n  " + warnings.join("\n  ") : "")
      end
    end

    def initialize(path, user:, telescope: nil, showcases_dir: nil)
      raise ArgumentError, "#{path} not found" unless File.exist?(path)

      @db = SQLite3::Database.new(path, readonly: true, results_as_hash: true)
      @user = user
      @telescope = telescope
      @showcases_dir = Pathname(showcases_dir || File.join(File.dirname(path), "showcases"))
      @report = Report.new(objects_merged: 0, objects_created: 0, objects_skipped: 0, projects_created: 0,
                           projects_skipped: 0, targets_created: 0, plans_created: 0, showcases: 0, warnings: [])
      @object_map = {}
    end

    def run
      ActiveRecord::Base.transaction do
        import_objects
        import_projects
        import_showcases
      end
      @report
    ensure
      @db&.close
    end

    private

    def table?(name)
      @db.get_first_value("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", [ name ]).to_i.positive?
    end

    def import_objects
      aliases = table?("object_aliases") ? @db.execute("SELECT object_id, alias_name, catalog FROM object_aliases").group_by { |r| r["object_id"] } : {}
      @db.execute("SELECT * FROM objects").each do |row|
        ra = row["ra"]&.to_f
        dec = row["dec"]&.to_f
        if ra.nil? || dec.nil?
          @report.objects_skipped += 1 # solar-system objects have no fixed coordinates
          next
        end

        names = [ row["primary_name"], *Array(aliases[row["id"]]).map { |a| a["alias_name"] } ].compact.uniq
        object = find_match(names, ra, dec)
        if object
          @report.objects_merged += 1
        else
          object = AstroObject.create!(
            primary_name: row["primary_name"], ra_deg: ra.round(5), dec_deg: dec.round(5), object_type: row["object_type"],
            magnitude: row["magnitude"], size_major_arcmin: row["size_major"], size_minor_arcmin: row["size_minor"],
            constellation: row["constellation"], source: "custom", source_ref: "astrodb:#{row['id']}", created_by: @user
          )
          @report.objects_created += 1
        end
        Array(aliases[row["id"]]).each { |a| object.add_alias(a["alias_name"], catalog: a["catalog"]) }
        object.add_alias(row["primary_name"])
        @object_map[row["id"]] = object
      end
    end

    def find_match(names, ra, dec)
      normalized = names.filter_map { |n| Catalogue::AliasNormalizer.normalize(n) }
      AstroObject.joins(:aliases).where(object_aliases: { normalized_name: normalized }).distinct.find do |candidate|
        candidate.coordinates? && Astro::Coordinates.separation(ra, dec, candidate.ra_deg.to_f, candidate.dec_deg.to_f) <= MERGE_RADIUS_DEG
      end
    end

    def import_projects
      return unless table?("projects")

      train = @telescope&.default_optical_train
      @db.execute("SELECT * FROM projects ORDER BY id").each do |row|
        if @user.projects.exists?(name: row["name"])
          @report.projects_skipped += 1
          @report.warnings << "project #{row['name'].inspect} already exists for #{@user.email}; skipped"
          next
        end

        targets = table?("project_targets") ? @db.execute("SELECT * FROM project_targets WHERE project_id = ? ORDER BY is_primary DESC, id", [ row["id"] ]) : []
        goal_lines = []
        project = @user.projects.create!(
          name: row["name"], description: row["description"], priority: row["priority"].to_i,
          status: @telescope ? project_status(row["status"]) : "planning"
        )

        targets.each_with_index do |pt, index|
          object = @object_map[pt["object_id"]]
          goals = parse_goals(pt["exposure_goals"])
          unless object
            @report.warnings << "#{project.name}: object ##{pt['object_id']} has no coordinates; target skipped"
            next
          end
          goal_lines << "#{object.primary_name}: " + goals.map { |f, s| "#{f} #{(s / 3600.0).round(1)} h" }.join(", ") if goals.any?
          next unless @telescope

          target = project.targets.create!(
            user: @user, telescope: @telescope, optical_train: train, astro_object: object, name: object.primary_name,
            ra_deg: object.ra_deg, dec_deg: object.dec_deg, is_primary: pt["is_primary"].to_i == 1 || index.zero?,
            notes: pt["notes"], status: :draft, priority: project.priority
          )
          @report.targets_created += 1
          goals.each { |filter, seconds| create_plan(target, row["id"], filter, seconds, train) }
        end

        if goal_lines.any?
          note = "Imported from astrophotography-database. Goals: #{goal_lines.join('; ')}."
          project.update!(description: [ project.description.presence, note ].compact.join("\n\n"))
        end
        @report.projects_created += 1
      end
    end

    def create_plan(target, astrodb_project_id, filter, goal_seconds, train)
      canonical = train&.filter_names&.any? ? train.canonical_filter(filter) : filter
      unless canonical
        @report.warnings << "#{target.project.name} / #{target.name}: filter #{filter.inspect} isn't on #{train.name}; add it (or an alias) and create the plan by hand"
        return
      end

      exposure = median_exposure(astrodb_project_id, filter) || DEFAULT_EXPOSURE_S
      target.exposure_plans.create!(filter: canonical, exposure_seconds: exposure, desired_count: (goal_seconds / exposure.to_f).ceil)
      @report.plans_created += 1
    end

    def median_exposure(astrodb_project_id, filter)
      return nil unless table?("project_images") && table?("images")

      values = @db.execute(<<~SQL, [ astrodb_project_id, filter ]).map { |r| r["exposure_time"].to_f }.select(&:positive?).sort
        SELECT i.exposure_time FROM project_images pi JOIN images i ON i.id = pi.image_id
        WHERE pi.project_id = ? AND i.filter_name = ? AND i.exposure_time IS NOT NULL
      SQL
      return nil if values.empty?

      mid = values.size / 2
      (values.size.odd? ? values[mid] : (values[mid - 1] + values[mid]) / 2.0).round
    end

    def parse_goals(raw)
      goals = raw.is_a?(String) ? JSON.parse(raw) : raw
      (goals || {}).filter_map { |f, s| [ f.to_s, s.to_f ] if s.to_f.positive? }
    rescue JSON::ParserError
      []
    end

    def project_status(status)
      PROJECT_STATUSES.include?(status) ? status : "active"
    end

    def import_showcases
      return unless table?("object_showcases")

      @db.execute("SELECT * FROM object_showcases").each do |row|
        object = @object_map[row["object_id"]]
        path = @showcases_dir.join(row["file_path"].to_s)
        next unless object
        next @report.warnings << "showcase #{path} not found" unless path.file?
        next if object.showcase&.image&.attached?

        showcase = object.showcase || object.build_showcase
        showcase.update!(source_type: row["source_type"] == "survey" ? "survey" : "upload", survey_name: row["survey_name"])
        showcase.image.attach(io: path.open("rb"), filename: path.basename.to_s, content_type: path.extname.casecmp?(".png") ? "image/png" : "image/jpeg")
        @report.showcases += 1
      end
    end
  end
end
