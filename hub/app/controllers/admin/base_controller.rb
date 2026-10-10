module Admin
  class BaseController < ApplicationController
    before_action :require_admin!

    layout "application"

    private

    def require_admin!
      return if current_user.admin?

      flash[:alert] = "You are not authorized to do that."
      # main_app: Mission Control's controllers inherit this one, and inside
      # that engine a bare root_path would be the engine's own root.
      redirect_to main_app.root_path
    end
  end
end
