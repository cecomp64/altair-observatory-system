# "Tonight" for one telescope: the viewer's schedulable targets ranked by
# imaging score, and catalogue objects worth suggesting (§7.3 dashboard).
class TonightPlanner
  Row = Struct.new(:target, :result, :score, keyword_init: true)
  Suggestion = Struct.new(:object, :result, :entry, keyword_init: true)

  attr_reader :telescope, :site, :night, :visibility

  def initialize(telescope, targets:)
    @telescope = telescope
    @site = Astro::Site.for(telescope)
    @visibility = Astro::Visibility.new(@site)
    @night = @visibility.night(telescope.night_for(Time.current))
    @targets = targets.select { |t| t.telescope_id == telescope.id }
  end

  def rows
    @rows ||= @targets.map do |target|
      result = @visibility.for_night(target.ra_deg, target.dec_deg, @night.date, min_altitude: target.effective_min_altitude_deg)
      score = Astro::Visibility.imaging_score(result, progress: target.percent_complete, priority: target.project.priority)
      Row.new(target: target, result: result, score: score)
    end.sort_by { |row| -row.score }
  end

  # Bright or large catalogue objects well placed tonight that none of the
  # given targets already point at.
  def suggestions(limit: 5, exclude_object_ids: [])
    entries = Astro::WellPlaced.for(telescope, date: @night.date)
    candidates = AstroObject.where(id: entries.keys).where.not(id: exclude_object_ids)
                            .where("magnitude <= 9 OR size_major_arcmin >= 15").pluck(:id)
    top = candidates.map { |id| entries[id] }.sort_by { |e| -e.score }.first(limit)
    objects = AstroObject.where(id: top.map(&:id)).index_by(&:id)
    top.map do |entry|
      object = objects[entry.id]
      Suggestion.new(object: object, entry: entry, result: @visibility.for_night(object.ra_deg, object.dec_deg, @night.date))
    end
  end
end
