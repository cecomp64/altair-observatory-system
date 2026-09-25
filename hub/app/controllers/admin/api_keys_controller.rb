module Admin
  class ApiKeysController < BaseController
    before_action :set_telescope

    def index
      @api_keys = @telescope.api_keys.order(created_at: :desc)
    end

    def new
      @api_key = @telescope.api_keys.new
    end

    def create
      @api_key = @telescope.api_keys.new(name: params[:api_key][:name])
      @api_key.generate_token!

      if @api_key.save
        # The plaintext token only ever exists in memory here — shown once.
        flash[:new_api_key_token] = @api_key.plaintext_token
        redirect_to admin_telescope_path(@telescope), notice: "API key created — copy the token now, it won't be shown again."
      else
        render :new, status: :unprocessable_content
      end
    end

    def destroy
      @telescope.api_keys.find(params[:id]).destroy
      redirect_to admin_telescope_path(@telescope), notice: "API key revoked."
    end

    private

    def set_telescope
      @telescope = Telescope.find_by_param!(params[:telescope_id])
    end
  end
end
