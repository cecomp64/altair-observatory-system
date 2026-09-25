module Admin
  class BaseController < ApplicationController
    before_action :require_admin!

    layout "application"

    private

    def require_admin!
      return if current_user.admin?

      flash[:alert] = "You are not authorized to do that."
      redirect_to root_path
    end
  end
end
