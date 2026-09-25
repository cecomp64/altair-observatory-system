# frozen_string_literal: true

# The catalogue is shared: everyone can browse it and add objects; only
# admins edit or delete, and only admins or the creator manage showcases.
class AstroObjectPolicy < ApplicationPolicy
  def index? = true
  def show? = true
  def create? = true
  def update? = user.admin?
  def destroy? = user.admin?

  def manage_showcase?
    user.admin? || record.created_by_id == user.id || record.showcase.nil?
  end

  class Scope < Scope
    def resolve = scope.all
  end
end
