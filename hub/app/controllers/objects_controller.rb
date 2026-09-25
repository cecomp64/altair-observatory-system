# The catalogue browser and object pages (ports of astrophotography-database's
# ObjectsPage, CataloguePage and ObjectDetailPage).
class ObjectsController < ApplicationController
  PER_PAGE = 25

  before_action :set_object, only: :show
  before_action :set_telescope, only: [ :index, :show ]

  def index
    authorize AstroObject
    scope = filtered_scope
    @well_placed = Astro::WellPlaced.for(@telescope) if @telescope && params[:tonight] == "1"
    scope = scope.where(id: @well_placed.keys) if @well_placed

    count = scope.unscope(:order).count
    @pagy, @objects = pagy(ordered(scope), limit: PER_PAGE, count: count)
    @objects = @objects.includes(:aliases)
    @facets = {
      types: AstroObject.where.not(object_type: nil).distinct.order(:object_type).pluck(:object_type),
      constellations: AstroObject.where.not(constellation: nil).distinct.order(:constellation).pluck(:constellation),
      catalogs: ObjectAlias.where.not(catalog: nil).distinct.order(:catalog).pluck(:catalog)
    }
    if @telescope
      @site = Astro::Site.for(@telescope)
      @night = Astro::Night.for(@site, @telescope.night_for(Time.current))
      @visibility = Astro::Visibility.new(@site)
    end
  end

  def show
    authorize @object
    if @object.coordinates? && @telescope
      @site = Astro::Site.for(@telescope)
      visibility = Astro::Visibility.new(@site)
      @date = params[:date].present? ? (Date.parse(params[:date]) rescue nil) : nil
      @date ||= @telescope.night_for(Time.current)
      @night = visibility.night(@date)
      @tonight = visibility.for_night(@object.ra_deg, @object.dec_deg, @date)
      @best = visibility.best_viewing(@object.ra_deg, @object.dec_deg, year: @date.year)
      @fits = fits_on_trains
    end
    @projects = policy_scope(Project).joins(:targets).where(targets: { astro_object_id: @object.id }).distinct.includes(:user)
  end

  def new
    authorize AstroObject
    @object = AstroObject.new
  end

  # Either a name to resolve (catalogue, then Telescopius) or custom coordinates.
  def create
    authorize AstroObject
    if params[:resolve].present?
      object, outcome = Catalogue::NameResolver.new.resolve(params[:resolve], created_by: current_user)
      return redirect_to(object_path(object), notice: outcome == :local ? "Already in the catalogue." : "Added from Telescopius.") if object

      @object = AstroObject.new(primary_name: params[:resolve])
      flash.now[:alert] = outcome == :not_configured ? "Online lookup isn't configured; enter coordinates instead." : "Couldn't find “#{params[:resolve]}”. Enter coordinates instead."
      return render :new, status: :unprocessable_content
    end

    @object = AstroObject.new(custom_params.merge(source: "custom", created_by: current_user))
    @object.ra_deg = CoordinateParser.parse_ra(params[:astro_object][:ra])
    @object.dec_deg = CoordinateParser.parse_dec(params[:astro_object][:dec])
    @object.errors.add(:base, "Enter valid coordinates (e.g. RA 05:35:17, Dec -05:23:28)") if @object.ra_deg.nil? || @object.dec_deg.nil?
    if @object.errors.none? && @object.save
      params[:astro_object][:aliases].to_s.split(",").each { |name| @object.add_alias(name) }
      redirect_to object_path(@object), notice: "Object added to the catalogue."
    else
      render :new, status: :unprocessable_content
    end
  end

  private

  def set_object
    @object = AstroObject.includes(:aliases, showcase: { image_attachment: :blob }).find(params[:id])
  end

  def set_telescope
    telescopes = policy_scope(Telescope).active.order(:name)
    @telescopes = telescopes.to_a
    @telescope = telescopes.find_by(slug: params[:telescope]) || (params[:telescope] == "none" ? nil : @telescopes.first)
  end

  def filtered_scope
    scope = params[:q].present? ? AstroObject.search(params[:q]) : AstroObject.all
    scope = scope.where(object_type: params[:type]) if params[:type].present?
    scope = scope.where(constellation: params[:constellation]) if params[:constellation].present?
    scope = scope.where(id: ObjectAlias.where(catalog: params[:catalog]).select(:astro_object_id)) if params[:catalog].present?
    scope = scope.where(magnitude: ..params[:mag_max].to_f) if params[:mag_max].present?
    scope = scope.where(size_major_arcmin: params[:size_min].to_f..) if params[:size_min].present?
    scope
  end

  def ordered(scope)
    case params[:sort]
    when "magnitude" then scope.reorder(Arel.sql("magnitude ASC NULLS LAST"), :primary_name)
    when "size" then scope.reorder(Arel.sql("size_major_arcmin DESC NULLS LAST"), :primary_name)
    when "score"
      return scope unless @well_placed

      ids = @well_placed.values.sort_by { |e| -e.score }.map(&:id)
      scope.reorder(Arel.sql(ActiveRecord::Base.sanitize_sql_array([ "array_position(ARRAY[?]::bigint[], astro_objects.id)", ids.presence || [ 0 ] ])))
    else params[:q].present? ? scope : scope.order(:primary_name)
    end
  end

  # Does the object fit each optical train's field of view?
  def fits_on_trains
    return {} unless @object.size_major_arcmin

    @telescope.optical_trains.select(&:active?).filter_map do |train|
      fov = train.fov_deg
      next unless fov

      [ train, @object.size_major_arcmin.to_f / 60.0 <= fov.min ]
    end.to_h
  end

  def custom_params
    params.require(:astro_object).permit(:primary_name, :object_type, :magnitude, :size_major_arcmin, :size_minor_arcmin, :constellation)
  end
end
