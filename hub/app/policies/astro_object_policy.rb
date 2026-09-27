# frozen_string_literal: true

# The catalogue is shared: everyone can browse it and add objects. Custom
# objects are private to their creator (and admins) until the creator shares
# them. Only admins edit or delete; admins or the creator manage showcases.
class AstroObjectPolicy < ApplicationPolicy
  def index? = true
  def show? = record.visible_to?(user)
  def create? = true
  def update? = user.admin?
  def destroy? = user.admin?

  # Share a custom object with every member, or make it private again.
  def share?
    record.custom? && (user.admin? || record.created_by_id == user.id)
  end

  def manage_showcase?
    show? && (user.admin? || record.created_by_id == user.id || record.showcase.nil?)
  end

  class Scope < Scope
    def resolve = scope.visible_to(user)
  end
end
