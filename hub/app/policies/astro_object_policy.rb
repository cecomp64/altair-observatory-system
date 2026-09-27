# frozen_string_literal: true

# The catalogue is shared: everyone can browse it; only admins add, edit or
# delete objects. A member's own objects come only from a project's custom
# target (ProjectWizardController), and stay private to them (and admins)
# until they share them. Admins or the creator manage showcases.
class AstroObjectPolicy < ApplicationPolicy
  def index? = true
  def show? = record.visible_to?(user)
  def create? = user.admin?
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
