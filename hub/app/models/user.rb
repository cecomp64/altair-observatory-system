class User < ApplicationRecord
  # Include default devise modules. Others available are:
  # :confirmable, :lockable, :timeoutable, :trackable and :omniauthable
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable

  enum :role, { member: 0, admin: 1 }

  has_many :projects, dependent: :destroy
  has_many :targets, dependent: :destroy

  validates :name, presence: true

  def admin?
    role == "admin"
  end

  def sjaa_member?
    sjaa_membership_number.present?
  end

  def display_name
    name.presence || email
  end
end
