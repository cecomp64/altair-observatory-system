Rails.application.routes.draw do
  devise_for :users

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  get "up" => "rails/health#show", as: :rails_health_check

  root "dashboard#index"

  resource :profile, only: [ :edit, :update ]

  resources :telescopes, only: [ :index, :show ]

  # Guided, step-by-step project creation wizard. These routes must be
  # declared before `resources :projects` below, otherwise `/projects/new`
  # would be swallowed by `projects#show` (id="new").
  controller :project_wizard do
    get    "projects/new",                   action: :objects,    as: :new_project
    patch  "projects/new",                   action: :update_objects
    post   "projects/new/objects",           action: :add_object, as: :project_wizard_add_object
    delete "projects/new/objects/:index",    action: :remove_object, as: :project_wizard_remove_object
    get    "projects/new/telescope",         action: :telescope,  as: :project_wizard_telescope
    patch  "projects/new/telescope",         action: :update_telescope
    get    "projects/new/exposures",         action: :exposures,  as: :project_wizard_exposures
    post   "projects/new/exposures",         action: :add_exposure_plan, as: :project_wizard_add_exposure_plan
    delete "projects/new/exposures/:index",  action: :remove_exposure_plan, as: :project_wizard_remove_exposure_plan
    patch  "projects/new/exposures",         action: :update_exposures
    get    "projects/new/review",            action: :review,     as: :project_wizard_review
    post   "projects/new/review",            action: :create,     as: :project_wizard_create
  end

  # The single-target wizard became the project wizard.
  get "targets/new", to: redirect("/projects/new"), as: :new_target

  resources :projects, only: [ :index, :show, :edit, :update ]

  resources :objects, only: [ :index, :show, :new, :create ] do
    resource :showcase, only: [ :create, :destroy ]
  end

  resources :targets, only: [ :index, :show ] do
    member do
      post :cancel
    end
  end

  namespace :admin do
    root to: "dashboard#index"

    resources :telescopes do
      resources :api_keys, only: [ :index, :new, :create, :destroy ]
      resources :optical_trains, except: [ :index, :show ]
    end

    resource :catalogue, only: :show, controller: "catalogue" do
      post :import
    end
  end

  namespace :api do
    namespace :v1 do
      resources :telescopes, only: [] do
        member do
          get :active_targets
        end
      end

      resources :targets, only: [] do
        member do
          patch :progress
          post :files
          post :events
        end
      end
    end
  end
end
