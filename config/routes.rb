Rails.application.routes.draw do
  devise_for :users

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  get "up" => "rails/health#show", as: :rails_health_check

  root "dashboard#index"

  resource :profile, only: [ :edit, :update ]

  resources :telescopes, only: [ :index, :show ]

  # Guided, step-by-step target creation wizard. Deliberately not a
  # `resources :targets, only: [:new, :create]` — each step is its own
  # small page (see ARCHITECTURE.md / UX notes in the controller). These
  # routes must be declared before `resources :targets` below, otherwise
  # `/targets/new` would be swallowed by `targets#show` (id="new").
  controller :target_wizard do
    get    "targets/new",                    action: :telescope,  as: :new_target
    patch  "targets/new",                    action: :update_telescope
    get    "targets/new/details",            action: :details,    as: :target_wizard_details
    patch  "targets/new/details",            action: :update_details
    get    "targets/new/exposures",          action: :exposures,  as: :target_wizard_exposures
    post   "targets/new/exposures",          action: :add_exposure_plan, as: :target_wizard_add_exposure_plan
    delete "targets/new/exposures/:index",   action: :remove_exposure_plan, as: :target_wizard_remove_exposure_plan
    patch  "targets/new/exposures",          action: :update_exposures
    get    "targets/new/review",             action: :review,     as: :target_wizard_review
    post   "targets/new/review",             action: :create,     as: :target_wizard_create
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
