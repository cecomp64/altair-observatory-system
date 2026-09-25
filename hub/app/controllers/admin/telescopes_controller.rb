module Admin
  class TelescopesController < BaseController
    before_action :set_telescope, only: [ :show, :edit, :update, :destroy ]

    def index
      @telescopes = Telescope.order(:name)
    end

    def show
      @api_keys = @telescope.api_keys.order(created_at: :desc)
      @optical_trains = @telescope.optical_trains.order(:key)
    end

    def new
      @telescope = Telescope.new
    end

    def create
      @telescope = Telescope.new(telescope_params)
      if @telescope.save
        redirect_to admin_telescope_path(@telescope), notice: "Telescope created."
      else
        render :new, status: :unprocessable_content
      end
    end

    def edit
    end

    def update
      if @telescope.update(telescope_params)
        redirect_to admin_telescope_path(@telescope), notice: "Telescope updated."
      else
        render :edit, status: :unprocessable_content
      end
    end

    def destroy
      @telescope.destroy
      redirect_to admin_telescopes_path, notice: "Telescope removed."
    end

    private

    def set_telescope
      @telescope = Telescope.find_by_param!(params[:id])
    end

    def telescope_params
      params.require(:telescope).permit(
        :name, :slug, :latitude, :longitude, :elevation_m,
        :active, :self_serve_submit, :description, :horizon_file,
        :timezone, :min_altitude_deg, :default_optical_train_id
      )
    end
  end
end
