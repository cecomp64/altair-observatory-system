module Admin
  class OpticalTrainsController < BaseController
    before_action :set_telescope
    before_action :set_optical_train, only: [ :edit, :update, :destroy ]

    def new
      @optical_train = @telescope.optical_trains.new
    end

    def create
      @optical_train = @telescope.optical_trains.new(optical_train_params)
      if @optical_train.save
        redirect_to admin_telescope_path(@telescope), notice: "Optical train created."
      else
        render :new, status: :unprocessable_content
      end
    end

    def edit
    end

    def update
      if @optical_train.update(optical_train_params)
        redirect_to admin_telescope_path(@telescope), notice: "Optical train updated."
      else
        render :edit, status: :unprocessable_content
      end
    end

    def destroy
      if @telescope.default_optical_train_id == @optical_train.id
        redirect_to admin_telescope_path(@telescope), alert: "Choose another default train before deleting this one."
      else
        @optical_train.destroy
        redirect_to admin_telescope_path(@telescope), notice: "Optical train removed."
      end
    end

    private

    def set_telescope
      @telescope = Telescope.find_by_param!(params[:telescope_id])
    end

    def set_optical_train
      @optical_train = @telescope.optical_trains.find_by!(key: params[:id])
    end

    def optical_train_params
      permitted = params.require(:optical_train).permit(
        :key, :name, :camera_name, :camera_type, :bayer_pattern, :pixel_size_um, :sensor_width_px,
        :sensor_height_px, :focal_length_mm, :has_rotator, :active, :filters_text,
        :header_aliases_telescope, :header_aliases_camera
      )
      telescope_aliases = permitted.delete(:header_aliases_telescope)
      camera_aliases = permitted.delete(:header_aliases_camera)
      unless telescope_aliases.nil? && camera_aliases.nil?
        permitted[:header_aliases] = {
          "telescope" => telescope_aliases.to_s.split(",").map(&:strip).reject(&:blank?),
          "camera" => camera_aliases.to_s.split(",").map(&:strip).reject(&:blank?)
        }
      end
      permitted
    end
  end
end
